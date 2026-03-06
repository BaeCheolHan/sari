"""SolidLSP probe 상태 갱신/백오프 계산 서비스."""

from __future__ import annotations

from solidlsp.ls_config import Language

from sari.services.collection.l5.solid_lsp_probe_mixin import _ProbeStateRecord


class LspProbeStateUpdateService:
    """extract 오류를 probe 상태로 반영하고 재시도 백오프를 계산한다."""

    def __init__(
        self,
        *,
        resolve_language,
        is_unavailable_probe_error,
        next_transient_backoff_sec,
        monotonic_now,
        probe_unavailable_backoff_initial_sec: float,
        probe_unavailable_backoff_mid_sec: float,
        probe_unavailable_backoff_cap_sec: float,
        probe_timeout_backoff_initial_sec: float,
        probe_timeout_backoff_mid_sec: float,
        probe_timeout_backoff_cap_sec: float,
    ) -> None:
        self._resolve_language = resolve_language
        self._is_unavailable_probe_error = is_unavailable_probe_error
        self._next_transient_backoff_sec = next_transient_backoff_sec
        self._monotonic_now = monotonic_now
        self._probe_unavailable_backoff_initial_sec = float(probe_unavailable_backoff_initial_sec)
        self._probe_unavailable_backoff_mid_sec = float(probe_unavailable_backoff_mid_sec)
        self._probe_unavailable_backoff_cap_sec = float(probe_unavailable_backoff_cap_sec)
        self._probe_timeout_backoff_initial_sec = float(probe_timeout_backoff_initial_sec)
        self._probe_timeout_backoff_mid_sec = float(probe_timeout_backoff_mid_sec)
        self._probe_timeout_backoff_cap_sec = float(probe_timeout_backoff_cap_sec)

    def record_extract_error(
        self,
        *,
        probe_state: dict[tuple[str, Language], _ProbeStateRecord],
        repo_root: str,
        relative_path: str,
        error_code: str,
        error_message: str,
    ) -> None:
        language = self._resolve_language(relative_path)
        if language is None:
            return
        key = (repo_root, language)
        now = self._monotonic_now()
        state = probe_state.get(key)
        if state is None:
            state = _ProbeStateRecord(status="IDLE", last_seen_monotonic=now)
            probe_state[key] = state
        state.last_seen_monotonic = now
        state.last_error_code = error_code
        state.last_error_message = error_message
        state.last_error_time_monotonic = now
        if error_code == "ERR_LSP_WORKSPACE_MISMATCH":
            state.status = "WORKSPACE_MISMATCH"
            state.next_retry_monotonic = float("inf")
            return
        if error_code in {"ERR_LSP_GLOBAL_SOFT_LIMIT", "ERR_LSP_SLOT_EXHAUSTED"}:
            state.status = "BACKPRESSURE_COOLDOWN"
            state.fail_count += 1
            state.next_retry_monotonic = now + self.next_probe_retry_backoff_sec(
                error_code=error_code,
                fail_count=state.fail_count,
            )
            return
        if not self._is_unavailable_probe_error(error_code):
            return
        state.status = "UNAVAILABLE_COOLDOWN"
        state.fail_count += 1
        state.next_retry_monotonic = now + self.next_probe_retry_backoff_sec(
            error_code=error_code,
            fail_count=state.fail_count,
        )

    def next_probe_retry_backoff_sec(self, *, error_code: str, fail_count: int) -> float:
        if error_code in {"ERR_LSP_SERVER_MISSING", "ERR_LSP_SERVER_SPAWN_FAILED", "ERR_RUNTIME_MISMATCH", "ERR_CONFIG_INVALID"}:
            if fail_count <= 2:
                return self._probe_unavailable_backoff_initial_sec
            if fail_count <= 4:
                return self._probe_unavailable_backoff_mid_sec
            return self._probe_unavailable_backoff_cap_sec
        if error_code in {"ERR_LSP_START_TIMEOUT", "ERR_RPC_TIMEOUT", "ERR_LSP_INTERACTIVE_TIMEOUT"}:
            if fail_count <= 1:
                return self._probe_timeout_backoff_initial_sec
            if fail_count == 2:
                return self._probe_timeout_backoff_mid_sec
            return self._probe_timeout_backoff_cap_sec
        if error_code in {"ERR_LSP_GLOBAL_SOFT_LIMIT", "ERR_LSP_SLOT_EXHAUSTED"}:
            return float(self._next_transient_backoff_sec(fail_count))
        return float(self._next_transient_backoff_sec(fail_count))
