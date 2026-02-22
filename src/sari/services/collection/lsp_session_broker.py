"""LSP 세션 브로커 (Phase 1 Baseline: lane/lease/공유 예산 기반)."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import threading
import time
import uuid
from typing import Callable, Iterator

from solidlsp.ls_config import Language


@dataclass(frozen=True)
class LspBrokerLanguageProfile:
    language: str
    hot_lanes: int
    backlog_lanes: int
    sticky_idle_ttl_sec: float
    switch_cooldown_sec: float
    min_lease_ms: int
    shared_budget_group: str | None = None


@dataclass(frozen=True)
class LspSessionLeaseResult:
    lease_id: str
    granted: bool
    reason: str
    language: str
    lsp_scope_root: str
    lane: str


@dataclass
class _ActiveLease:
    lease_id: str
    language: str
    lsp_scope_root: str
    lane: str
    started_at_monotonic: float


@dataclass
class _LaneState:
    assigned_scope: str | None = None
    last_switch_at_monotonic: float = 0.0


@dataclass(frozen=True)
class LspSessionBrokerSnapshot:
    active_sessions_by_language: dict[str, int]
    active_sessions_by_budget_group: dict[str, int]


class LspSessionBroker:
    """Profiled 언어의 lane lease/budget만 담당한다.

    Phase 1 Baseline:
    - lane cap / shared budget group cap
    - min lease / switch cooldown
    - lease context manager + try/finally release
    - optional cost/DRR는 behavior 미사용
    """

    def __init__(
        self,
        *,
        profiles: dict[str, LspBrokerLanguageProfile],
        max_standby_sessions_per_lang: int,
        max_standby_sessions_per_budget_group: int,
        backlog_min_share: float = 0.0,
        optional_scaffolding_enabled: bool = False,
        now_monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._profiles = {k.lower(): v for k, v in profiles.items()}
        self._max_standby_sessions_per_lang = int(max_standby_sessions_per_lang)
        self._max_standby_sessions_per_budget_group = int(max_standby_sessions_per_budget_group)
        self._backlog_min_share = min(1.0, max(0.0, float(backlog_min_share)))
        self._optional_scaffolding_enabled = bool(optional_scaffolding_enabled)
        self._now_monotonic = now_monotonic or time.monotonic
        self._lock = threading.Lock()
        self._leases: dict[str, _ActiveLease] = {}
        self._lane_state: dict[tuple[str, str], _LaneState] = {}  # (language, lane)
        self._language_lane_active_caps: dict[tuple[str, str], int] = {}
        self._budget_group_active_caps: dict[str, int] = {}
        self._budget_group_members: dict[str, set[str]] = {}
        self._backlog_demand_pending: set[str] = set()
        self._hot_streak_since_backlog: dict[str, int] = {}
        self._optional_cost_class_counts: dict[str, int] = {}
        self._optional_cost_obs_total = 0
        self._optional_drr_obs_total = 0
        self._optional_drr_quantum_by_lane: dict[str, int] = {"hot": 0, "backlog": 0}
        self._optional_drr_deficit_by_key: dict[str, int] = {}
        for lang, profile in self._profiles.items():
            if profile.shared_budget_group:
                self._budget_group_members.setdefault(profile.shared_budget_group, set()).add(lang)

    def _fairness_key_for(self, lang_key: str, profile: LspBrokerLanguageProfile) -> str:
        if profile.shared_budget_group:
            return f"group:{profile.shared_budget_group}"
        return f"lang:{lang_key}"

    def _max_hot_streak_before_backlog(self) -> int | None:
        share = self._backlog_min_share
        if share <= 0.0 or share >= 1.0:
            if share >= 1.0:
                return 0
            return None
        return max(0, math.floor((1.0 - share) / share))

    def set_language_active_cap(self, language: str, *, lane: str, cap: int) -> None:
        with self._lock:
            self._language_lane_active_caps[(language.lower(), lane.lower())] = max(0, int(cap))

    def set_budget_group_active_cap(self, group: str, cap: int) -> None:
        with self._lock:
            self._budget_group_active_caps[group] = max(0, int(cap))

    def acquire_lease(
        self,
        *,
        language: Language,
        lsp_scope_root: str,
        lane: str,
        hotness_score: float,
        pending_jobs_in_scope: int,
    ) -> LspSessionLeaseResult:
        del hotness_score
        lang_key = language.value.lower()
        lane_key = lane.lower()
        profile = self._profiles.get(lang_key)
        if profile is None:
            return LspSessionLeaseResult(
                lease_id="",
                granted=False,
                reason="unprofiled_language",
                language=lang_key,
                lsp_scope_root=lsp_scope_root,
                lane=lane_key,
            )
        now = self._now_monotonic()
        if self._optional_scaffolding_enabled:
            predicted_cost_class, cost_estimate = self._predict_cost_scaffold(
                lane=lane_key,
                language=lang_key,
                pending_jobs_in_scope=pending_jobs_in_scope,
            )
            self._record_optional_scaffolding_observation(
                lane=lane_key,
                fairness_key=self._fairness_key_for(lang_key, profile),
                predicted_cost_class=predicted_cost_class,
                cost_estimate=cost_estimate,
            )
        with self._lock:
            fairness_key = self._fairness_key_for(lang_key, profile)
            hot_streak_limit = self._max_hot_streak_before_backlog()
            if lane_key == "hot" and hot_streak_limit is not None and fairness_key in self._backlog_demand_pending:
                hot_streak = int(self._hot_streak_since_backlog.get(fairness_key, 0))
                if hot_streak >= hot_streak_limit:
                    return LspSessionLeaseResult("", False, "starvation_guard", lang_key, lsp_scope_root, lane_key)

            # same-scope reuse
            for active in self._leases.values():
                if active.language == lang_key and active.lsp_scope_root == lsp_scope_root:
                    lease_id = uuid.uuid4().hex
                    self._leases[lease_id] = _ActiveLease(
                        lease_id=lease_id,
                        language=lang_key,
                        lsp_scope_root=lsp_scope_root,
                        lane=lane_key,
                        started_at_monotonic=now,
                    )
                    return LspSessionLeaseResult(
                        lease_id=lease_id,
                        granted=True,
                        reason="active_reuse",
                        language=lang_key,
                        lsp_scope_root=lsp_scope_root,
                        lane=lane_key,
                    )

            lane_state_key = (lang_key, lane_key)
            lane_state = self._lane_state.setdefault(lane_state_key, _LaneState())
            if lane_state.assigned_scope is not None and lane_state.assigned_scope != lsp_scope_root:
                if (now - lane_state.last_switch_at_monotonic) < max(0.0, profile.switch_cooldown_sec):
                    if lane_key == "backlog" and pending_jobs_in_scope > 0:
                        self._backlog_demand_pending.add(fairness_key)
                    return LspSessionLeaseResult("", False, "cooldown", lang_key, lsp_scope_root, lane_key)

            # language lane cap
            cap = self._language_lane_active_caps.get(
                lane_state_key,
                (profile.hot_lanes if lane_key == "hot" else profile.backlog_lanes),
            )
            active_same_lane = sum(1 for item in self._leases.values() if item.language == lang_key and item.lane == lane_key)
            if active_same_lane >= cap:
                # if cap full and a different scope is active in this lane, min-lease blocks immediate preemption
                lane_active = next(
                    (
                        item for item in self._leases.values()
                        if item.language == lang_key and item.lane == lane_key
                    ),
                    None,
                )
                if lane_active is not None and lane_active.lsp_scope_root != lsp_scope_root:
                    min_lease_sec = max(0.0, float(profile.min_lease_ms) / 1000.0)
                    if (now - lane_active.started_at_monotonic) < min_lease_sec:
                        if lane_key == "backlog" and pending_jobs_in_scope > 0:
                            self._backlog_demand_pending.add(fairness_key)
                        return LspSessionLeaseResult("", False, "min_lease", lang_key, lsp_scope_root, lane_key)
                if lane_key == "backlog" and pending_jobs_in_scope > 0:
                    self._backlog_demand_pending.add(fairness_key)
                return LspSessionLeaseResult("", False, "budget_blocked", lang_key, lsp_scope_root, lane_key)

            # shared budget group cap (Phase 1: budget only, runtime sharing 없음)
            budget_group = profile.shared_budget_group
            if budget_group:
                group_cap = self._budget_group_active_caps.get(budget_group, self._max_standby_sessions_per_budget_group)
                members = self._budget_group_members.get(budget_group, set())
                active_group = sum(1 for item in self._leases.values() if item.language in members)
                if active_group >= group_cap:
                    if lane_key == "backlog" and pending_jobs_in_scope > 0:
                        self._backlog_demand_pending.add(fairness_key)
                    return LspSessionLeaseResult("", False, "budget_group_blocked", lang_key, lsp_scope_root, lane_key)

            lease_id = uuid.uuid4().hex
            self._leases[lease_id] = _ActiveLease(
                lease_id=lease_id,
                language=lang_key,
                lsp_scope_root=lsp_scope_root,
                lane=lane_key,
                started_at_monotonic=now,
            )
            if lane_state.assigned_scope != lsp_scope_root:
                lane_state.assigned_scope = lsp_scope_root
                lane_state.last_switch_at_monotonic = now
            if lane_key == "backlog":
                self._backlog_demand_pending.discard(fairness_key)
                self._hot_streak_since_backlog[fairness_key] = 0
            elif lane_key == "hot":
                self._hot_streak_since_backlog[fairness_key] = int(self._hot_streak_since_backlog.get(fairness_key, 0)) + 1
            return LspSessionLeaseResult(
                lease_id=lease_id,
                granted=True,
                reason="admitted",
                language=lang_key,
                lsp_scope_root=lsp_scope_root,
                lane=lane_key,
            )

    def release_lease(self, lease: LspSessionLeaseResult | str) -> bool:
        lease_id = lease if isinstance(lease, str) else lease.lease_id
        if lease_id == "":
            return False
        with self._lock:
            return self._leases.pop(lease_id, None) is not None

    @contextmanager
    def lease(
        self,
        *,
        language: Language,
        lsp_scope_root: str,
        lane: str,
        hotness_score: float,
        pending_jobs_in_scope: int,
    ) -> Iterator[LspSessionLeaseResult]:
        lease = self.acquire_lease(
            language=language,
            lsp_scope_root=lsp_scope_root,
            lane=lane,
            hotness_score=hotness_score,
            pending_jobs_in_scope=pending_jobs_in_scope,
        )
        try:
            yield lease
        finally:
            if lease.granted:
                self.release_lease(lease)

    def get_snapshot(self) -> LspSessionBrokerSnapshot:
        with self._lock:
            active_by_lang: dict[str, int] = {}
            for lease in self._leases.values():
                active_by_lang[lease.language] = int(active_by_lang.get(lease.language, 0)) + 1
            active_by_group: dict[str, int] = {}
            for group, members in self._budget_group_members.items():
                active_by_group[group] = sum(1 for lease in self._leases.values() if lease.language in members)
            return LspSessionBrokerSnapshot(
                active_sessions_by_language=active_by_lang,
                active_sessions_by_budget_group=active_by_group,
            )

    def get_metrics(self) -> dict[str, int]:
        snap = self.get_snapshot()
        metrics: dict[str, int] = {}
        for lang, count in snap.active_sessions_by_language.items():
            metrics[f"broker_active_sessions_{lang}"] = int(count)
        for group, count in snap.active_sessions_by_budget_group.items():
            metrics[f"broker_active_budget_group_{group}"] = int(count)
        if self._backlog_min_share > 0.0:
            with self._lock:
                metrics["broker_backlog_demand_pending_keys"] = len(self._backlog_demand_pending)
        if self._optional_scaffolding_enabled:
            with self._lock:
                metrics["broker_optional_cost_obs_total"] = int(self._optional_cost_obs_total)
                metrics["broker_optional_drr_obs_total"] = int(self._optional_drr_obs_total)
                for klass, count in self._optional_cost_class_counts.items():
                    metrics[f"broker_optional_cost_class_{klass}"] = int(count)
                for lane, quantum in self._optional_drr_quantum_by_lane.items():
                    metrics[f"broker_optional_drr_quantum_{lane}"] = int(quantum)
                metrics["broker_optional_drr_deficit_keys"] = len(self._optional_drr_deficit_by_key)
        return metrics

    def _predict_cost_scaffold(
        self,
        *,
        lane: str,
        language: str,
        pending_jobs_in_scope: int,
    ) -> tuple[str, int]:
        """Optional scaffolding only: cost class/cost_estimate를 계산하지만 선택 로직엔 사용하지 않는다."""
        lane_key = lane.lower()
        pending = max(0, int(pending_jobs_in_scope))
        lang_key = language.lower()
        if lane_key == "backlog" and pending >= 100:
            return ("l", 100)
        if lang_key == "java" and pending >= 10:
            return ("l", max(10, pending))
        if pending <= 3:
            return ("s", 1)
        return ("m", min(50, max(2, pending)))

    def _record_optional_scaffolding_observation(
        self,
        *,
        lane: str,
        fairness_key: str,
        predicted_cost_class: str,
        cost_estimate: int,
    ) -> None:
        """Optional scaffolding only: DRR/cost 상태를 metrics용으로만 기록한다."""
        del cost_estimate  # Phase 1 Optional: metrics-only scaffolding, scheduling behavior 미사용
        lane_key = lane.lower()
        with self._lock:
            self._optional_cost_obs_total += 1
            self._optional_cost_class_counts[predicted_cost_class] = int(
                self._optional_cost_class_counts.get(predicted_cost_class, 0)
            ) + 1
            self._optional_drr_obs_total += 1
            default_quantum = 2 if lane_key == "hot" else 1
            self._optional_drr_quantum_by_lane[lane_key] = int(self._optional_drr_quantum_by_lane.get(lane_key, default_quantum) or default_quantum)
            self._optional_drr_deficit_by_key[fairness_key] = int(
                self._optional_drr_deficit_by_key.get(fairness_key, 0)
            ) + default_quantum
