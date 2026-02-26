from __future__ import annotations

from solidlsp.ls_config import Language

from sari.services.collection.lsp_probe_state_update_service import LspProbeStateUpdateService
from sari.services.collection.solid_lsp_probe_mixin import _ProbeStateRecord


def _resolver(path: str):
    if path.endswith('.py'):
        return Language.PYTHON
    return None


def test_workspace_mismatch_sets_infinite_retry() -> None:
    service = LspProbeStateUpdateService(
        resolve_language=_resolver,
        is_unavailable_probe_error=lambda _code: True,
        next_transient_backoff_sec=lambda _n: 9.0,
        monotonic_now=lambda: 100.0,
        probe_unavailable_backoff_initial_sec=180.0,
        probe_unavailable_backoff_mid_sec=600.0,
        probe_unavailable_backoff_cap_sec=1800.0,
        probe_timeout_backoff_initial_sec=30.0,
        probe_timeout_backoff_mid_sec=60.0,
        probe_timeout_backoff_cap_sec=120.0,
    )
    state_map: dict[tuple[str, Language], _ProbeStateRecord] = {}

    service.record_extract_error(
        probe_state=state_map,
        repo_root='/repo',
        relative_path='a.py',
        error_code='ERR_LSP_WORKSPACE_MISMATCH',
        error_message='mismatch',
    )

    rec = state_map[('/repo', Language.PYTHON)]
    assert rec.status == 'WORKSPACE_MISMATCH'
    assert rec.next_retry_monotonic == float('inf')


def test_unavailable_error_enters_cooldown_and_increments_fail_count() -> None:
    service = LspProbeStateUpdateService(
        resolve_language=_resolver,
        is_unavailable_probe_error=lambda _code: True,
        next_transient_backoff_sec=lambda _n: 9.0,
        monotonic_now=lambda: 200.0,
        probe_unavailable_backoff_initial_sec=180.0,
        probe_unavailable_backoff_mid_sec=600.0,
        probe_unavailable_backoff_cap_sec=1800.0,
        probe_timeout_backoff_initial_sec=30.0,
        probe_timeout_backoff_mid_sec=60.0,
        probe_timeout_backoff_cap_sec=120.0,
    )
    state_map: dict[tuple[str, Language], _ProbeStateRecord] = {}

    service.record_extract_error(
        probe_state=state_map,
        repo_root='/repo',
        relative_path='a.py',
        error_code='ERR_LSP_SERVER_MISSING',
        error_message='missing',
    )

    rec = state_map[('/repo', Language.PYTHON)]
    assert rec.status == 'UNAVAILABLE_COOLDOWN'
    assert rec.fail_count == 1
    assert rec.next_retry_monotonic == 380.0


def test_timeout_backoff_progression() -> None:
    service = LspProbeStateUpdateService(
        resolve_language=_resolver,
        is_unavailable_probe_error=lambda _code: False,
        next_transient_backoff_sec=lambda _n: 9.0,
        monotonic_now=lambda: 1.0,
        probe_unavailable_backoff_initial_sec=180.0,
        probe_unavailable_backoff_mid_sec=600.0,
        probe_unavailable_backoff_cap_sec=1800.0,
        probe_timeout_backoff_initial_sec=30.0,
        probe_timeout_backoff_mid_sec=60.0,
        probe_timeout_backoff_cap_sec=120.0,
    )

    assert service.next_probe_retry_backoff_sec(error_code='ERR_RPC_TIMEOUT', fail_count=1) == 30.0
    assert service.next_probe_retry_backoff_sec(error_code='ERR_RPC_TIMEOUT', fail_count=2) == 60.0
    assert service.next_probe_retry_backoff_sec(error_code='ERR_RPC_TIMEOUT', fail_count=3) == 120.0
