# Test Pruning Candidates (Aggressive-Prep, 2026-02-12)

## Applied now

### Removed
- `tests/test_cli_deep.py`
- `tests/test_mcp_api_smoke.py`
- `tests/test_registry_tools_smoke_minimal.py`
- `tests/test_final_push.py`
- `tests/test_business_logic_smoke.py`

Reason:
- 중복: CLI 관련 동일 시나리오가 `tests/test_cli_commands.py`, `tests/test_cli_extra.py`에 이미 존재.
- 무의미 검증 포함: `test_cli_doctor_logic`은 `assert len(out.getvalue()) >= 0`로 항상 참.
- 삭제 후 검증: `uv run pytest -q tests/test_cli_commands.py tests/test_cli_extra.py` → `26 passed`.
- 삭제 후 검증: `uv run pytest -q tests/test_mcp_contract_drift_regression.py tests/test_search_v3_response.py tests/test_mcp_tools_extra.py` → `55 passed`.
- 삭제 후 검증: `uv run pytest -q tests/test_core_main.py tests/test_indexer.py tests/test_mcp_contract_drift_regression.py tests/test_read_enforce_gate.py` → `28 passed`.
- 삭제 근거: `read_file`/`dry_run_diff`가 `SEARCH_REF_REQUIRED` 정책(`candidate_id` 필수)으로 전환되어 기존 smoke 기대와 구조적으로 불일치.
- 삭제 근거: `test_final_push.py`는 동작 검증보다 커버리지 호출 나열 성격이 강함.
- 삭제 근거: `test_business_logic_smoke.py`는 `/tmp` 고정 경로를 쓰는 단일 smoke로, 계약/통합 테스트와 중복 커버.

## Next Candidates (Review before delete)

### Tier A (done)
1. `tests/test_registry_tools_smoke_minimal.py` 제거 완료
- 사유: legacy smoke 기대가 현 read-policy와 불일치, coverage는 `test_read_enforce_gate.py` 및 unified read 계열에서 유지.

### Tier C (keep)
3. `tests/test_smart_daemon_e2e.py`
- 느리지만 실제 subprocess/port lifecycle 검증으로 대체 어려움.

4. `tests/test_doctor_self_healing.py`
- 느리지만 registry corruption/stale healing 시나리오 검증.

5. `tests/test_daemon_session_deep.py`
- 고정 포트(`49991`) 사용으로 flaky 발생하던 케이스를 동적 포트 + 응답 대기 루프로 안정화 완료.
- 유지 대상.

## Guardrails for further pruning
- 삭제 전 동일 시나리오 대체 테스트 존재 확인.
- `--maxfail=1` + 대상 모듈 테스트 통과 확인.
- e2e/실프로세스 테스트는 대체 증거 없으면 유지.
