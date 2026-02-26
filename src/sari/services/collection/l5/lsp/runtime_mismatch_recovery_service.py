"""Runtime mismatch 복구/판정 서비스."""

from __future__ import annotations

import logging

from solidlsp.ls_config import Language

from sari.core.exceptions import DaemonError
from sari.services.collection.solid_lsp_probe_mixin import _ProbeStateRecord

log = logging.getLogger(__name__)


class LspRuntimeMismatchRecoveryService:
    """ERR_RUNTIME_MISMATCH 복구와 extract 오류 기반 강제복구 판정을 담당한다."""

    def __init__(self, *, resolve_language, monotonic_now) -> None:
        self._resolve_language = resolve_language
        self._monotonic_now = monotonic_now

    def should_force_recover_from_extract_error(
        self,
        *,
        probe_state: dict[tuple[str, Language], _ProbeStateRecord],
        repo_root: str,
        relative_path: str,
        error_code: str,
        probe_timeout_window_sec: float,
    ) -> bool:
        language = self._resolve_language(relative_path)
        if language is None:
            return False
        key = (repo_root, language)
        now = self._monotonic_now()
        state = probe_state.get(key)
        if state is None:
            return False
        if error_code in {"ERR_BROKEN_PIPE", "ERR_SERVER_EXITED", "ERR_INIT_FAILED"}:
            return state.status in {"READY_L0", "WARMING"}
        if error_code != "ERR_RPC_TIMEOUT":
            return False
        if state.last_error_code == "ERR_RPC_TIMEOUT" and state.last_error_time_monotonic is not None:
            if (now - state.last_error_time_monotonic) <= probe_timeout_window_sec:
                return state.status in {"READY_L0", "WARMING"}
        state.last_error_code = "ERR_RPC_TIMEOUT"
        state.last_error_time_monotonic = now
        return False

    def recover_from_runtime_mismatch(
        self,
        *,
        hub: object,
        runtime_mismatch_last_restart_at: dict[tuple[str, str], float],
        runtime_mismatch_restart_cooldown_sec: float,
        repo_root: str,
        relative_path: str,
    ) -> bool:
        language = self._resolve_language(relative_path)
        if language is None:
            return False
        key = (repo_root, language.value)
        now = self._monotonic_now()
        last_restart = runtime_mismatch_last_restart_at.get(key)
        if last_restart is not None and (now - last_restart) < runtime_mismatch_restart_cooldown_sec:
            return False
        force_restart = getattr(hub, "force_restart", None)
        if not callable(force_restart):
            return False
        try:
            force_restart(language=language, repo_root=repo_root, request_kind="indexing")
            runtime_mismatch_last_restart_at[key] = now
            return True
        except (DaemonError, RuntimeError, OSError, ValueError, TypeError):
            log.warning(
                "runtime mismatch auto-restart failed(repo=%s, path=%s, language=%s)",
                repo_root,
                relative_path,
                language.value,
                exc_info=True,
            )
            return False
