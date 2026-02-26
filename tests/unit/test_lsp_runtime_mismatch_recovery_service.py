from __future__ import annotations

from solidlsp.ls_config import Language

from sari.services.collection.lsp_runtime_mismatch_recovery_service import LspRuntimeMismatchRecoveryService
from sari.services.collection.solid_lsp_probe_mixin import _ProbeStateRecord


def _resolver(path: str):
    if path.endswith('.py'):
        return Language.PYTHON
    return None


def test_should_force_recover_on_broken_pipe_ready() -> None:
    service = LspRuntimeMismatchRecoveryService(resolve_language=_resolver, monotonic_now=lambda: 100.0)
    state = {('/repo', Language.PYTHON): _ProbeStateRecord(status='READY_L0', last_seen_monotonic=1.0)}

    out = service.should_force_recover_from_extract_error(
        probe_state=state,
        repo_root='/repo',
        relative_path='a.py',
        error_code='ERR_BROKEN_PIPE',
        probe_timeout_window_sec=30.0,
    )

    assert out is True


def test_should_force_recover_on_second_timeout_within_window() -> None:
    now = {"v": 100.0}
    service = LspRuntimeMismatchRecoveryService(resolve_language=_resolver, monotonic_now=lambda: now["v"])
    rec = _ProbeStateRecord(status='WARMING', last_seen_monotonic=1.0)
    state = {('/repo', Language.PYTHON): rec}

    first = service.should_force_recover_from_extract_error(
        probe_state=state,
        repo_root='/repo',
        relative_path='a.py',
        error_code='ERR_RPC_TIMEOUT',
        probe_timeout_window_sec=30.0,
    )
    now["v"] = 110.0
    second = service.should_force_recover_from_extract_error(
        probe_state=state,
        repo_root='/repo',
        relative_path='a.py',
        error_code='ERR_RPC_TIMEOUT',
        probe_timeout_window_sec=30.0,
    )

    assert first is False
    assert second is True


class _Hub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def force_restart(self, *, language: Language, repo_root: str, request_kind: str) -> None:
        self.calls.append((language.value, repo_root, request_kind))


def test_recover_from_runtime_mismatch_obeys_cooldown() -> None:
    now = {"v": 100.0}
    service = LspRuntimeMismatchRecoveryService(resolve_language=_resolver, monotonic_now=lambda: now["v"])
    hub = _Hub()
    restart_at: dict[tuple[str, str], float] = {}

    first = service.recover_from_runtime_mismatch(
        hub=hub,
        runtime_mismatch_last_restart_at=restart_at,
        runtime_mismatch_restart_cooldown_sec=2.0,
        repo_root='/repo',
        relative_path='a.py',
    )
    second = service.recover_from_runtime_mismatch(
        hub=hub,
        runtime_mismatch_last_restart_at=restart_at,
        runtime_mismatch_restart_cooldown_sec=2.0,
        repo_root='/repo',
        relative_path='a.py',
    )

    assert first is True
    assert second is False
    assert len(hub.calls) == 1
