"""L3 bootstrap mode 정책/커버리지 계산 전담 서비스."""

from __future__ import annotations


class L3BootstrapModeService:
    """bootstrap 정책 조회, coverage 계산, 모드 전이를 담당한다."""

    def __init__(self, *, file_repo: object, policy_repo: object | None = None) -> None:
        self._file_repo = file_repo
        self._policy_repo = policy_repo

    def resolve_bootstrap_policy(self) -> tuple[bool, int, int, int]:
        policy_repo = self._policy_repo
        if policy_repo is None:
            return (False, 1, 9500, 1800)
        policy = policy_repo.get_policy()
        return (
            bool(policy.bootstrap_mode_enabled),
            max(1, int(policy.bootstrap_l3_worker_count)),
            max(1, min(10000, int(policy.bootstrap_exit_min_l2_coverage_bps))),
            max(60, int(policy.bootstrap_exit_max_sec)),
        )

    def compute_coverage_bps(self) -> tuple[int, int]:
        state_counts = self._file_repo.get_enrich_state_counts()
        total = int(sum(state_counts.values()))
        if total <= 0:
            return (0, 0)
        l3_skipped = int(state_counts.get("L3_SKIPPED", 0))
        l2_ready = (
            int(state_counts.get("BODY_READY", 0))
            + int(state_counts.get("LSP_READY", 0))
            + int(state_counts.get("TOOL_READY", 0))
            + l3_skipped
        )
        l3_ready = int(state_counts.get("LSP_READY", 0)) + int(state_counts.get("TOOL_READY", 0))
        l3_total = max(0, total - l3_skipped)
        l2_bps = int(l2_ready * 10000 / total)
        l3_bps = 10000 if l3_total <= 0 else int(l3_ready * 10000 / l3_total)
        return (l2_bps, l3_bps)

    def refresh_indexing_mode(
        self,
        *,
        current_mode: str,
        bootstrap_started_at: float,
        monotonic_now: float,
    ) -> str:
        bootstrap_enabled, _, bootstrap_exit_l2_bps, bootstrap_exit_max_sec = self.resolve_bootstrap_policy()
        if not bootstrap_enabled:
            return "steady"
        elapsed_sec = monotonic_now - bootstrap_started_at
        l2_bps, l3_bps = self.compute_coverage_bps()
        reenter_l2_bps = max(1, bootstrap_exit_l2_bps - 700)
        if elapsed_sec >= float(bootstrap_exit_max_sec):
            return "steady"
        if current_mode == "steady" and l2_bps < bootstrap_exit_l2_bps:
            return "bootstrap_l2_priority"
        if current_mode == "steady":
            return "steady"
        if current_mode == "bootstrap_balanced" and l2_bps < reenter_l2_bps:
            return "bootstrap_l2_priority"
        if current_mode == "bootstrap_l2_priority" and l2_bps >= bootstrap_exit_l2_bps:
            return "bootstrap_balanced"
        if current_mode == "bootstrap_balanced" and l3_bps >= 9990:
            return "steady"
        return current_mode
