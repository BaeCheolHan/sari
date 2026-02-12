# Test Pruning Candidates (Aggressive-Prep, 2026-02-12)

## Applied now

### Removed
- `tests/test_cli_deep.py`

Reason:
- 중복: CLI 관련 동일 시나리오가 `tests/test_cli_commands.py`, `tests/test_cli_extra.py`에 이미 존재.
- 무의미 검증 포함: `test_cli_doctor_logic`은 `assert len(out.getvalue()) >= 0`로 항상 참.
- 삭제 후 검증: `uv run pytest -q tests/test_cli_commands.py tests/test_cli_extra.py` → `26 passed`.

## Next Candidates (Review before delete)

### Tier A (likely removable)
1. `tests/test_mcp_api_smoke.py`
- 성격: DB 직접 smoke (search/repo_candidates/context_snippet)
- 겹침: `tests/test_mcp_contract_drift_regression.py`, `tests/test_registry_tools_smoke_minimal.py`에서 유사 경로를 이미 커버.
- 제안: 파일 삭제 대신 핵심 1개만 유지하거나 contract drift 쪽으로 통합.

### Tier B (merge recommended)
2. `tests/test_registry_tools_smoke_minimal.py` + `tests/test_mcp_contract_drift_regression.py::test_registry_smoke_for_contract_drift_tools`
- 성격: registry execute smoke 반복
- 제안: 공통 fixture화 + 단일 smoke matrix로 통합.

### Tier C (keep)
3. `tests/test_smart_daemon_e2e.py`
- 느리지만 실제 subprocess/port lifecycle 검증으로 대체 어려움.

4. `tests/test_doctor_self_healing.py`
- 느리지만 registry corruption/stale healing 시나리오 검증.

## Guardrails for further pruning
- 삭제 전 동일 시나리오 대체 테스트 존재 확인.
- `--maxfail=1` + 대상 모듈 테스트 통과 확인.
- e2e/실프로세스 테스트는 대체 증거 없으면 유지.
