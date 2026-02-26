from __future__ import annotations

from dataclasses import dataclass

from sari.services.collection.l3.l3_bootstrap_mode_service import L3BootstrapModeService


@dataclass
class _Policy:
    bootstrap_mode_enabled: bool = True
    bootstrap_l3_worker_count: int = 3
    bootstrap_exit_min_l2_coverage_bps: int = 9500
    bootstrap_exit_max_sec: int = 1800


class _PolicyRepo:
    def __init__(self, policy: _Policy) -> None:
        self._policy = policy

    def get_policy(self) -> _Policy:
        return self._policy


class _FileRepo:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts

    def get_enrich_state_counts(self) -> dict[str, int]:
        return dict(self._counts)


def test_compute_coverage_bps_uses_l3_skipped_rule() -> None:
    service = L3BootstrapModeService(file_repo=_FileRepo({"BODY_READY": 2, "LSP_READY": 1, "TOOL_READY": 1, "L3_SKIPPED": 1}))

    l2_bps, l3_bps = service.compute_coverage_bps()

    assert l2_bps == 10000
    assert l3_bps == 5000


def test_refresh_indexing_mode_enters_and_exits_bootstrap_l2_priority() -> None:
    policy_repo = _PolicyRepo(_Policy())
    file_repo = _FileRepo({"BODY_READY": 1, "LSP_READY": 0, "TOOL_READY": 0, "L3_SKIPPED": 0, "PENDING": 9})
    service = L3BootstrapModeService(file_repo=file_repo, policy_repo=policy_repo)

    mode = service.refresh_indexing_mode(current_mode="steady", bootstrap_started_at=100.0, monotonic_now=110.0)
    assert mode == "bootstrap_l2_priority"

    file_repo._counts = {"BODY_READY": 95, "LSP_READY": 5, "TOOL_READY": 0, "L3_SKIPPED": 0}
    mode = service.refresh_indexing_mode(current_mode=mode, bootstrap_started_at=100.0, monotonic_now=120.0)
    assert mode == "bootstrap_balanced"


def test_refresh_indexing_mode_forces_steady_after_timeout() -> None:
    policy_repo = _PolicyRepo(_Policy(bootstrap_exit_max_sec=60))
    file_repo = _FileRepo({"BODY_READY": 0, "LSP_READY": 0, "TOOL_READY": 0, "L3_SKIPPED": 0})
    service = L3BootstrapModeService(file_repo=file_repo, policy_repo=policy_repo)

    mode = service.refresh_indexing_mode(current_mode="bootstrap_l2_priority", bootstrap_started_at=100.0, monotonic_now=170.0)

    assert mode == "steady"
