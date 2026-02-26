"""파이프라인 운영 제어 서비스를 구현한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections.abc import Callable

from sari.core.exceptions import ErrorContext, ValidationError
from sari.core.models import (
    DeadJobActionResultDTO,
    DeadJobItemDTO,
    PipelineAlertSnapshotDTO,
    PipelineAutoControlStateDTO,
    PipelinePolicyDTO,
    now_iso8601_utc,
)
from sari.db.repositories.pipeline_control_state_repository import PipelineControlStateRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository


class PipelineControlService:
    """운영 정책/알람/DEAD 작업 제어를 담당한다."""

    def __init__(
        self,
        policy_repo: PipelinePolicyRepository,
        event_repo: PipelineJobEventRepository,
        queue_repo: FileEnrichQueueRepository,
        control_state_repo: PipelineControlStateRepository,
        set_l5_admission_mode: Callable[[bool, bool], None] | None = None,
        set_search_resolve_symbols_default: Callable[[bool], None] | None = None,
    ) -> None:
        """서비스 의존성을 저장한다."""
        self._policy_repo = policy_repo
        self._event_repo = event_repo
        self._queue_repo = queue_repo
        self._control_state_repo = control_state_repo
        self._set_l5_admission_mode = set_l5_admission_mode
        self._set_search_resolve_symbols_default = set_search_resolve_symbols_default
        self._last_l5_admission_enforced: bool | None = None
        self._last_resolve_symbols_default: bool | None = None

    def get_policy(self) -> PipelinePolicyDTO:
        """운영 정책을 조회한다."""
        return self._policy_repo.get_policy()

    def update_policy(
        self,
        deletion_hold: bool | None = None,
        l3_p95_threshold_ms: int | None = None,
        dead_ratio_threshold_bps: int | None = None,
        enrich_worker_count: int | None = None,
        watcher_queue_max: int | None = None,
        watcher_overflow_rescan_cooldown_sec: int | None = None,
        bootstrap_mode_enabled: bool | None = None,
        bootstrap_l3_worker_count: int | None = None,
        bootstrap_l3_queue_max: int | None = None,
        bootstrap_exit_min_l2_coverage_bps: int | None = None,
        bootstrap_exit_max_sec: int | None = None,
        alert_window_sec: int | None = None,
    ) -> PipelinePolicyDTO:
        """운영 정책을 갱신한다."""
        if l3_p95_threshold_ms is not None and l3_p95_threshold_ms <= 0:
            raise ValidationError(ErrorContext(code="ERR_POLICY_INVALID", message="l3_p95_threshold_ms는 1 이상이어야 합니다"))
        if dead_ratio_threshold_bps is not None and dead_ratio_threshold_bps <= 0:
            raise ValidationError(ErrorContext(code="ERR_POLICY_INVALID", message="dead_ratio_threshold_bps는 1 이상이어야 합니다"))
        if enrich_worker_count is not None and enrich_worker_count <= 0:
            raise ValidationError(ErrorContext(code="ERR_POLICY_INVALID", message="enrich_worker_count는 1 이상이어야 합니다"))
        if watcher_queue_max is not None and watcher_queue_max < 100:
            raise ValidationError(ErrorContext(code="ERR_POLICY_INVALID", message="watcher_queue_max는 100 이상이어야 합니다"))
        if watcher_overflow_rescan_cooldown_sec is not None and watcher_overflow_rescan_cooldown_sec < 1:
            raise ValidationError(
                ErrorContext(code="ERR_POLICY_INVALID", message="watcher_overflow_rescan_cooldown_sec는 1 이상이어야 합니다")
            )
        if bootstrap_l3_worker_count is not None and bootstrap_l3_worker_count <= 0:
            raise ValidationError(ErrorContext(code="ERR_POLICY_INVALID", message="bootstrap_l3_worker_count는 1 이상이어야 합니다"))
        if bootstrap_l3_queue_max is not None and bootstrap_l3_queue_max <= 0:
            raise ValidationError(ErrorContext(code="ERR_POLICY_INVALID", message="bootstrap_l3_queue_max는 1 이상이어야 합니다"))
        if bootstrap_exit_min_l2_coverage_bps is not None and (
            bootstrap_exit_min_l2_coverage_bps <= 0 or bootstrap_exit_min_l2_coverage_bps > 10000
        ):
            raise ValidationError(
                ErrorContext(code="ERR_POLICY_INVALID", message="bootstrap_exit_min_l2_coverage_bps는 1..10000 범위여야 합니다")
            )
        if bootstrap_exit_max_sec is not None and bootstrap_exit_max_sec < 60:
            raise ValidationError(ErrorContext(code="ERR_POLICY_INVALID", message="bootstrap_exit_max_sec는 60 이상이어야 합니다"))
        if alert_window_sec is not None and alert_window_sec < 60:
            raise ValidationError(ErrorContext(code="ERR_POLICY_INVALID", message="alert_window_sec는 60 이상이어야 합니다"))
        return self._policy_repo.update_policy(
            deletion_hold=deletion_hold,
            l3_p95_threshold_ms=l3_p95_threshold_ms,
            dead_ratio_threshold_bps=dead_ratio_threshold_bps,
            enrich_worker_count=enrich_worker_count,
            watcher_queue_max=watcher_queue_max,
            watcher_overflow_rescan_cooldown_sec=watcher_overflow_rescan_cooldown_sec,
            bootstrap_mode_enabled=bootstrap_mode_enabled,
            bootstrap_l3_worker_count=bootstrap_l3_worker_count,
            bootstrap_l3_queue_max=bootstrap_l3_queue_max,
            bootstrap_exit_min_l2_coverage_bps=bootstrap_exit_min_l2_coverage_bps,
            bootstrap_exit_max_sec=bootstrap_exit_max_sec,
            alert_window_sec=alert_window_sec,
        )

    def get_alert_status(self, now_iso: str | None = None) -> PipelineAlertSnapshotDTO:
        """최근 윈도우 기준 알람 스냅샷을 계산한다."""
        policy = self._policy_repo.get_policy()
        window_sec = self._policy_repo.get_alert_window_sec()
        now_dt = _parse_iso(now_iso) if now_iso is not None else datetime.now(timezone.utc)
        from_dt = now_dt - timedelta(seconds=window_sec)
        events = self._event_repo.list_window_events(from_iso=from_dt.isoformat())
        latencies: list[int] = []
        dead_count = 0
        for event in events:
            latency = int(event.get("latency_ms", 0))
            latencies.append(latency)
            if str(event.get("status", "")) == "DEAD":
                dead_count += 1
        event_count = len(events)
        l3_p95_ms = _percentile_95(latencies)
        dead_ratio_bps = 0
        if event_count > 0:
            dead_ratio_bps = int((dead_count * 10_000) / event_count)

        state = "ok"
        if dead_ratio_bps >= policy.dead_ratio_threshold_bps or l3_p95_ms >= policy.l3_p95_threshold_ms:
            state = "warn"
        if dead_ratio_bps >= policy.dead_ratio_threshold_bps * 2 or l3_p95_ms >= policy.l3_p95_threshold_ms * 2:
            state = "critical"

        return PipelineAlertSnapshotDTO(
            state=state,
            window_seconds=window_sec,
            event_count=event_count,
            dead_count=dead_count,
            dead_ratio_bps=dead_ratio_bps,
            l3_p95_ms=l3_p95_ms,
            threshold_dead_ratio_bps=policy.dead_ratio_threshold_bps,
            threshold_l3_p95_ms=policy.l3_p95_threshold_ms,
            policy=policy,
        )

    def get_queue_snapshot(self) -> dict[str, int]:
        """현재 큐 상태 스냅샷을 반환한다."""
        return self._queue_repo.get_status_counts()

    def list_dead_jobs(self, repo_root: str, limit: int) -> list[DeadJobItemDTO]:
        """DEAD 작업 목록을 조회한다."""
        if limit <= 0:
            raise ValidationError(ErrorContext(code="ERR_INVALID_LIMIT", message="limit는 1 이상이어야 합니다"))
        return self._queue_repo.list_dead(repo_root=repo_root, limit=limit)

    def requeue_dead_jobs(
        self,
        repo_root: str,
        limit: int,
        now_iso: str | None = None,
        all_scopes: bool = False,
    ) -> DeadJobActionResultDTO:
        """DEAD 작업을 재큐잉한다."""
        if limit <= 0:
            raise ValidationError(ErrorContext(code="ERR_INVALID_LIMIT", message="limit는 1 이상이어야 합니다"))
        applied_now = now_iso if now_iso is not None else now_iso8601_utc()
        if all_scopes:
            count = self._queue_repo.requeue_dead_all(now_iso=applied_now)
            scope = "all"
        else:
            count = self._queue_repo.requeue_dead(repo_root=repo_root, limit=limit, now_iso=applied_now)
            scope = "repo"
        return DeadJobActionResultDTO(
            requeued_count=count,
            purged_count=0,
            queue_snapshot=self._queue_repo.get_status_counts(),
            executed_at=applied_now,
            repo_scope=scope,
        )

    def purge_dead_jobs(self, repo_root: str, limit: int, all_scopes: bool = False) -> DeadJobActionResultDTO:
        """DEAD 작업을 삭제한다."""
        if limit <= 0:
            raise ValidationError(ErrorContext(code="ERR_INVALID_LIMIT", message="limit는 1 이상이어야 합니다"))
        applied_now = now_iso8601_utc()
        if all_scopes:
            count = self._queue_repo.purge_dead_all()
            scope = "all"
        else:
            count = self._queue_repo.purge_dead(repo_root=repo_root, limit=limit)
            scope = "repo"
        return DeadJobActionResultDTO(
            requeued_count=0,
            purged_count=count,
            queue_snapshot=self._queue_repo.get_status_counts(),
            executed_at=applied_now,
            repo_scope=scope,
        )

    def get_auto_control_state(self) -> PipelineAutoControlStateDTO:
        """자동제어 상태를 조회한다."""
        return self._control_state_repo.get_state()

    def get_stage_rollout_state(self) -> dict[str, object]:
        """자동제어 상태(last_action)에서 stage rollout 상태를 추출한다."""
        state = self._control_state_repo.get_state()
        return _parse_stage_rollout_last_action(state.last_action)

    def set_auto_hold_enabled(self, enabled: bool) -> PipelineAutoControlStateDTO:
        """자동제어 활성화를 설정한다."""
        action = "auto_enabled" if enabled else "auto_disabled"
        return self._control_state_repo.update_state(auto_hold_enabled=enabled, last_action=action)

    def set_l5_admission_mode(self, *, shadow_enabled: bool, enforced: bool) -> dict[str, bool]:
        """L5 admission 런타임 모드를 수동으로 설정한다."""
        if self._set_l5_admission_mode is None:
            raise ValidationError(
                ErrorContext(code="ERR_L5_ADMISSION_CONTROL_UNAVAILABLE", message="l5 admission runtime control is unavailable")
            )
        self._set_l5_admission_mode(shadow_enabled=bool(shadow_enabled), enforced=bool(enforced))
        self._last_l5_admission_enforced = bool(enforced)
        self._control_state_repo.update_state(
            last_action=f"l5_admission:set:shadow={int(bool(shadow_enabled))}:enforced={int(bool(enforced))}"
        )
        return {"shadow_enabled": bool(shadow_enabled), "enforced": bool(enforced)}

    def evaluate_auto_hold(self, now_iso: str | None = None) -> dict[str, object]:
        """알람 상태를 기반으로 hold 자동 제어를 수행한다."""
        state = self._control_state_repo.get_state()
        if not state.auto_hold_enabled:
            return {"action": "AUTO_DISABLED", "changed": False, "auto_control": state.to_dict()}

        policy = self.get_policy()
        alert = self.get_alert_status(now_iso=now_iso)

        if alert.state in {"warn", "critical"} and not policy.deletion_hold:
            self.update_policy(deletion_hold=True)
            updated = self._control_state_repo.update_state(
                auto_hold_active=True,
                last_action=f"hold_enabled_by_auto:{alert.state}",
            )
            return {"action": "HOLD_ENABLED", "changed": True, "auto_control": updated.to_dict(), "alert": alert.to_dict()}

        if alert.state == "ok" and state.auto_hold_active and policy.deletion_hold:
            self.update_policy(deletion_hold=False)
            updated = self._control_state_repo.update_state(
                auto_hold_active=False,
                last_action="hold_released_by_auto",
            )
            return {"action": "HOLD_RELEASED", "changed": True, "auto_control": updated.to_dict(), "alert": alert.to_dict()}

        updated = self._control_state_repo.update_state(last_action=f"no_change:{alert.state}")
        return {"action": "NO_CHANGE", "changed": False, "auto_control": updated.to_dict(), "alert": alert.to_dict()}

    def evaluate_stage_rollout(self, summary: dict[str, object] | None) -> dict[str, object]:
        """Stage exit 결과로 런타임 토글을 적용한다."""
        self._hydrate_last_rollout_targets_from_state()
        stage_exit = _extract_stage_exit(summary=summary)
        if stage_exit is None:
            self._control_state_repo.update_state(last_action="stage_rollout:no_report")
            return {"action": "NO_REPORT", "changed": False}

        stage_a = stage_exit.get("stage_a_to_b")
        stage_b = stage_exit.get("stage_b_to_c")
        stage_a_passed = isinstance(stage_a, dict) and bool(stage_a.get("passed"))
        stage_b_passed = isinstance(stage_b, dict) and bool(stage_b.get("passed"))

        target_l5_enforced = bool(stage_b_passed)
        target_resolve_symbols_default = not bool(stage_a_passed)
        changed = False

        if self._set_l5_admission_mode is not None:
            self._set_l5_admission_mode(shadow_enabled=True, enforced=target_l5_enforced)
            if self._last_l5_admission_enforced is None or self._last_l5_admission_enforced != target_l5_enforced:
                changed = True
            self._last_l5_admission_enforced = target_l5_enforced
        if self._set_search_resolve_symbols_default is not None:
            self._set_search_resolve_symbols_default(target_resolve_symbols_default)
            if (
                self._last_resolve_symbols_default is None
                or self._last_resolve_symbols_default != target_resolve_symbols_default
            ):
                changed = True
            self._last_resolve_symbols_default = target_resolve_symbols_default

        action = "ROLLOUT_APPLIED" if changed else "NO_CHANGE"
        self._control_state_repo.update_state(
            last_action=(
                "stage_rollout:"
                f"{action.lower()}:"
                f"stage_a={int(stage_a_passed)}:"
                f"stage_b={int(stage_b_passed)}:"
                f"l5_enforced={int(target_l5_enforced)}:"
                f"resolve_symbols_default={int(target_resolve_symbols_default)}"
            )
        )
        return {
            "action": action,
            "changed": changed,
            "stage_a_passed": stage_a_passed,
            "stage_b_passed": stage_b_passed,
            "l5_admission_enforced": target_l5_enforced,
            "resolve_symbols_default": target_resolve_symbols_default,
        }

    def _hydrate_last_rollout_targets_from_state(self) -> None:
        """프로세스 재시작 후에도 stage rollout 직전 토글 상태를 복원한다."""
        if self._last_l5_admission_enforced is not None and self._last_resolve_symbols_default is not None:
            return
        state = self._control_state_repo.get_state()
        parsed = _parse_stage_rollout_last_action(state.last_action)
        if not bool(parsed.get("available")):
            return
        l5_value = parsed.get("l5_admission_enforced")
        resolve_value = parsed.get("resolve_symbols_default")
        if isinstance(l5_value, bool):
            self._last_l5_admission_enforced = l5_value
        if isinstance(resolve_value, bool):
            self._last_resolve_symbols_default = resolve_value


def _parse_iso(raw: str) -> datetime:
    """ISO8601 문자열을 UTC datetime으로 변환한다."""
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_stage_exit(summary: dict[str, object] | None) -> dict[str, object] | None:
    """pipeline_perf summary에서 workspace_real stage_exit를 추출한다."""
    if not isinstance(summary, dict):
        return None
    datasets = summary.get("datasets")
    if isinstance(datasets, list):
        for item in datasets:
            if not isinstance(item, dict):
                continue
            if str(item.get("dataset_type")) != "workspace_real":
                continue
            integrity = item.get("integrity")
            if not isinstance(integrity, dict):
                continue
            stage_exit = integrity.get("stage_exit")
            if isinstance(stage_exit, dict):
                return stage_exit
    stage_exit_top = summary.get("stage_exit")
    if isinstance(stage_exit_top, dict):
        return stage_exit_top
    return None


def _parse_stage_rollout_last_action(last_action: str | None) -> dict[str, object]:
    """last_action 문자열에서 stage rollout 상태를 파싱한다."""
    if not isinstance(last_action, str) or not last_action.startswith("stage_rollout:"):
        return {
            "available": False,
            "action": None,
            "stage_a_passed": None,
            "stage_b_passed": None,
            "l5_admission_enforced": None,
            "resolve_symbols_default": None,
        }
    parts = last_action.split(":")
    action = parts[1] if len(parts) >= 2 else None
    parsed: dict[str, object] = {
        "available": True,
        "action": action,
        "stage_a_passed": None,
        "stage_b_passed": None,
        "l5_admission_enforced": None,
        "resolve_symbols_default": None,
    }
    for token in parts[2:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        parsed_value: object
        if value in {"0", "1"}:
            parsed_value = value == "1"
        else:
            parsed_value = value
        if key == "stage_a":
            parsed["stage_a_passed"] = parsed_value
        elif key == "stage_b":
            parsed["stage_b_passed"] = parsed_value
        elif key == "l5_enforced":
            parsed["l5_admission_enforced"] = parsed_value
        elif key == "resolve_symbols_default":
            parsed["resolve_symbols_default"] = parsed_value
    return parsed


def _percentile_95(values: list[int]) -> int:
    """정수 리스트의 95퍼센타일 값을 계산한다."""
    if len(values) == 0:
        return 0
    ordered = sorted(values)
    index = (len(ordered) * 95 + 99) // 100 - 1
    if index < 0:
        index = 0
    if index >= len(ordered):
        index = len(ordered) - 1
    return int(ordered[index])
