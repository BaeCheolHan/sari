from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO, L4AdmissionDecisionDTO, L5ReasonCode, L5RejectReason, L5RequestMode
from sari.services.collection.l5_admission_runtime_service import L5AdmissionRuntimeService, L5AdmissionRuntimeState


@dataclass
class _FakeL4AdmissionService:
    decision: L4AdmissionDecisionDTO

    def evaluate_batch(self, **_: object) -> L4AdmissionDecisionDTO:
        return self.decision


class _FakeLspBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool, str]] = []

    def schedule_probe_for_file(self, *, repo_root: str, relative_path: str, force: bool, trigger: str) -> None:
        self.calls.append((repo_root, relative_path, force, trigger))


def _job() -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/tmp/repo",
        relative_path="src/a.py",
        content_hash="h1",
        priority=100,
        enqueue_source="watcher",
        status="pending",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_l5_admission_runtime_rate_limit_short_circuit() -> None:
    now = 1000.0
    state = L5AdmissionRuntimeState(
        total_decisions=0,
        total_admitted=0,
        batch_decisions=0,
        batch_admitted=0,
        calls_per_min_per_lang_max=1,
        admitted_timestamps_by_lang={"python": deque([now - 1.0])},
        cooldown_until_by_scope_file={},
        reject_counts_by_reason={reason: 0 for reason in L5RejectReason},
        cost_units_by_reason={},
        cost_units_by_language={},
        cost_units_by_workspace={},
    )
    svc = L5AdmissionRuntimeService(
        l4_admission_service=_FakeL4AdmissionService(
            decision=L4AdmissionDecisionDTO(admit_l5=True, reason_code=L5ReasonCode.GOLDENSET_COVERAGE)
        ),
        lsp_backend=_FakeLspBackend(),
        monotonic_now=lambda: now,
    )

    decision = svc.evaluate_batch_for_job(state=state, job=_job(), language="python")

    assert decision is not None
    assert decision.admit_l5 is False
    assert decision.reject_reason is L5RejectReason.PRESSURE_RATE_EXCEEDED
    assert state.total_decisions == 1
    assert state.batch_decisions == 1
    assert state.reject_counts_by_reason[L5RejectReason.PRESSURE_RATE_EXCEEDED] == 1
    assert state.cost_units_by_language.get("python", 0.0) > 0.0


def test_l5_admission_runtime_admit_records_metrics_and_probe() -> None:
    now = 2000.0
    backend = _FakeLspBackend()
    decision = L4AdmissionDecisionDTO(
        admit_l5=True,
        reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
        mode=L5RequestMode.BATCH,
        workspace_uid="/tmp/repo",
        budget_cost=2,
    )
    state = L5AdmissionRuntimeState(
        total_decisions=0,
        total_admitted=0,
        batch_decisions=0,
        batch_admitted=0,
        calls_per_min_per_lang_max=30,
        admitted_timestamps_by_lang={},
        cooldown_until_by_scope_file={"/tmp/repo:h1": now + 60.0},
        reject_counts_by_reason={reason: 0 for reason in L5RejectReason},
        cost_units_by_reason={},
        cost_units_by_language={},
        cost_units_by_workspace={},
    )
    svc = L5AdmissionRuntimeService(
        l4_admission_service=_FakeL4AdmissionService(decision=decision),
        lsp_backend=backend,
        monotonic_now=lambda: now,
    )

    resolved = svc.evaluate_batch_for_job(state=state, job=_job(), language="python")

    assert resolved is not None
    assert resolved.admit_l5 is True
    assert state.total_decisions == 1
    assert state.total_admitted == 1
    assert state.batch_decisions == 1
    assert state.batch_admitted == 1
    assert len(state.admitted_timestamps_by_lang["python"]) == 1
    assert "/tmp/repo:h1" not in state.cooldown_until_by_scope_file
    assert backend.calls == [("/tmp/repo", "src/a.py", True, "l4_admission")]
