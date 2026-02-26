"""L5 admission 런타임 상태 갱신/집계를 담당한다."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO, L4AdmissionDecisionDTO, L5ReasonCode, L5RejectReason, L5RequestMode


class _BatchAdmissionPort:
    def evaluate_batch(
        self,
        *,
        repo_root: str,
        language_key: str,
        total_rate: float,
        batch_rate: float,
        cooldown_active: bool,
        reason_code: L5ReasonCode,
    ) -> L4AdmissionDecisionDTO: ...


@dataclass
class L5AdmissionRuntimeState:
    total_decisions: int
    total_admitted: int
    batch_decisions: int
    batch_admitted: int
    calls_per_min_per_lang_max: int
    admitted_timestamps_by_lang: dict[str, deque[float]]
    cooldown_until_by_scope_file: dict[str, float]
    reject_counts_by_reason: dict[L5RejectReason, int]
    cost_units_by_reason: dict[str, float]
    cost_units_by_language: dict[str, float]
    cost_units_by_workspace: dict[str, float]


class L5AdmissionRuntimeService:
    """배치 admission 판정 이후 런타임 카운터/쿨다운/코스트를 갱신한다."""

    def __init__(
        self,
        *,
        l4_admission_service: _BatchAdmissionPort,
        lsp_backend: object,
        monotonic_now: Callable[[], float],
    ) -> None:
        self._l4_admission_service = l4_admission_service
        self._lsp_backend = lsp_backend
        self._monotonic_now = monotonic_now

    def evaluate_batch_for_job(
        self,
        *,
        state: L5AdmissionRuntimeState,
        job: FileEnrichJobDTO,
        language: str,
    ) -> L4AdmissionDecisionDTO | None:
        lang_key = str(language or "").strip().lower()
        if lang_key == "":
            return None
        now_mono = float(self._monotonic_now())
        cooldown_key = self._build_cooldown_key(job=job)
        cooldown_until = float(state.cooldown_until_by_scope_file.get(cooldown_key, 0.0))
        cooldown_active = now_mono < cooldown_until
        recent_lang_calls = self._count_recent_admitted(
            admitted_timestamps_by_lang=state.admitted_timestamps_by_lang,
            lang_key=lang_key,
            now_mono=now_mono,
        )
        if recent_lang_calls >= state.calls_per_min_per_lang_max:
            workspace_uid = self._normalize_workspace_uid(job.repo_root)
            state.total_decisions += 1
            state.batch_decisions += 1
            decision = L4AdmissionDecisionDTO(
                admit_l5=False,
                reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
                reject_reason=L5RejectReason.PRESSURE_RATE_EXCEEDED,
                mode=L5RequestMode.BATCH,
                workspace_uid=workspace_uid,
                budget_cost=1,
                cooldown_until=self._upsert_cooldown(
                    cooldown_until_by_scope_file=state.cooldown_until_by_scope_file,
                    cooldown_key=cooldown_key,
                    now_mono=now_mono,
                    reject_reason=L5RejectReason.PRESSURE_RATE_EXCEEDED,
                ),
            )
            self._record_reject(reject_counts_by_reason=state.reject_counts_by_reason, decision=decision)
            self._record_cost_units(
                cost_units_by_reason=state.cost_units_by_reason,
                cost_units_by_language=state.cost_units_by_language,
                cost_units_by_workspace=state.cost_units_by_workspace,
                decision=decision,
                language_key=lang_key,
                workspace_uid=workspace_uid,
            )
            return decision

        state.total_decisions += 1
        state.batch_decisions += 1
        total_rate = 0.0 if state.total_decisions <= 0 else float(state.total_admitted) / float(state.total_decisions)
        batch_rate = 0.0 if state.batch_decisions <= 0 else float(state.batch_admitted) / float(state.batch_decisions)
        try:
            decision = self._l4_admission_service.evaluate_batch(
                repo_root=job.repo_root,
                language_key=lang_key,
                total_rate=total_rate,
                batch_rate=batch_rate,
                cooldown_active=cooldown_active,
                reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
            )
        except TypeError:
            decision = self._l4_admission_service.evaluate_batch(
                repo_root=job.repo_root,
                language_key=lang_key,
                total_rate=total_rate,
                batch_rate=batch_rate,
                reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
            )

        if decision.admit_l5:
            state.total_admitted += 1
            state.batch_admitted += 1
            self._record_admitted(
                admitted_timestamps_by_lang=state.admitted_timestamps_by_lang,
                lang_key=lang_key,
                now_mono=now_mono,
            )
            self._schedule_probe(job=job)
            state.cooldown_until_by_scope_file.pop(cooldown_key, None)
        else:
            reject_reason = decision.reject_reason
            if reject_reason is not None:
                updated_until = self._upsert_cooldown(
                    cooldown_until_by_scope_file=state.cooldown_until_by_scope_file,
                    cooldown_key=cooldown_key,
                    now_mono=now_mono,
                    reject_reason=reject_reason,
                )
                decision = L4AdmissionDecisionDTO(
                    admit_l5=decision.admit_l5,
                    reason_code=decision.reason_code,
                    reject_reason=decision.reject_reason,
                    mode=decision.mode,
                    workspace_uid=decision.workspace_uid,
                    budget_cost=decision.budget_cost,
                    cooldown_until=updated_until,
                    primary_cause=decision.primary_cause,
                    reject_stage=decision.reject_stage,
                    policy_version=decision.policy_version,
                )
            self._record_reject(reject_counts_by_reason=state.reject_counts_by_reason, decision=decision)

        self._record_cost_units(
            cost_units_by_reason=state.cost_units_by_reason,
            cost_units_by_language=state.cost_units_by_language,
            cost_units_by_workspace=state.cost_units_by_workspace,
            decision=decision,
            language_key=lang_key,
            workspace_uid=self._normalize_workspace_uid(job.repo_root),
        )
        return decision

    def _schedule_probe(self, *, job: FileEnrichJobDTO) -> None:
        scheduler = getattr(self._lsp_backend, "schedule_probe_for_file", None)
        if not callable(scheduler):
            return
        try:
            scheduler(
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                force=True,
                trigger="l4_admission",
            )
        except (RuntimeError, OSError, ValueError, TypeError):
            return

    @staticmethod
    def _normalize_workspace_uid(repo_root: str) -> str:
        return repo_root.strip()

    @staticmethod
    def _file_fingerprint_from_content_hash(content_hash: str) -> str:
        normalized = str(content_hash or "").strip()
        if normalized != "":
            return normalized
        return "missing-content-hash"

    def _build_cooldown_key(self, *, job: FileEnrichJobDTO) -> str:
        workspace_uid = self._normalize_workspace_uid(job.repo_root)
        file_fingerprint = self._file_fingerprint_from_content_hash(job.content_hash)
        return f"{workspace_uid}:{file_fingerprint}"

    @staticmethod
    def _upsert_cooldown(
        *,
        cooldown_until_by_scope_file: dict[str, float],
        cooldown_key: str,
        now_mono: float,
        reject_reason: L5RejectReason,
    ) -> float:
        duration_sec_by_reason: dict[L5RejectReason, float] = {
            L5RejectReason.PRESSURE_RATE_EXCEEDED: 30.0,
            L5RejectReason.PRESSURE_BURST_EXCEEDED: 10.0,
            L5RejectReason.PRESSURE_WORKSPACE_EXCEEDED: 20.0,
            L5RejectReason.COOLDOWN_ACTIVE: 15.0,
        }
        duration = float(duration_sec_by_reason.get(reject_reason, 10.0))
        until = max(float(now_mono), float(cooldown_until_by_scope_file.get(cooldown_key, 0.0))) + duration
        cooldown_until_by_scope_file[cooldown_key] = until
        return until

    @staticmethod
    def _record_reject(*, reject_counts_by_reason: dict[L5RejectReason, int], decision: L4AdmissionDecisionDTO) -> None:
        reject_reason = decision.reject_reason
        if reject_reason is None:
            return
        reject_counts_by_reason[reject_reason] = int(reject_counts_by_reason.get(reject_reason, 0)) + 1

    @staticmethod
    def _count_recent_admitted(
        *,
        admitted_timestamps_by_lang: dict[str, deque[float]],
        lang_key: str,
        now_mono: float,
    ) -> int:
        window_start = float(now_mono) - 60.0
        bucket = admitted_timestamps_by_lang.get(lang_key)
        if bucket is None:
            return 0
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        return len(bucket)

    @staticmethod
    def _record_admitted(
        *,
        admitted_timestamps_by_lang: dict[str, deque[float]],
        lang_key: str,
        now_mono: float,
    ) -> None:
        bucket = admitted_timestamps_by_lang.setdefault(lang_key, deque())
        bucket.append(float(now_mono))

    @staticmethod
    def _record_cost_units(
        *,
        cost_units_by_reason: dict[str, float],
        cost_units_by_language: dict[str, float],
        cost_units_by_workspace: dict[str, float],
        decision: L4AdmissionDecisionDTO,
        language_key: str,
        workspace_uid: str,
    ) -> None:
        cost_units = float(max(0, int(decision.budget_cost)))
        if cost_units <= 0.0:
            return
        reason = "none"
        if decision.reason_code is not None:
            reason = decision.reason_code.value
        cost_units_by_reason[reason] = cost_units_by_reason.get(reason, 0.0) + cost_units
        normalized_language = str(language_key or "").strip().lower()
        if normalized_language != "":
            cost_units_by_language[normalized_language] = cost_units_by_language.get(normalized_language, 0.0) + cost_units
        normalized_workspace = str(workspace_uid or "").strip()
        if normalized_workspace != "":
            cost_units_by_workspace[normalized_workspace] = cost_units_by_workspace.get(normalized_workspace, 0.0) + cost_units
