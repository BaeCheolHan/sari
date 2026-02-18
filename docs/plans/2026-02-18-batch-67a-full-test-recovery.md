# Batch-67A Full Test Recovery

## 목표
- 전체 테스트 실패 4건을 복구한다.
- 숨김 정책/예외 정책/데몬 생명주기 테스트 정합성을 맞춘다.

## 변경 내용
- `src/sari/daemon_process.py`
  - `_is_parent_alive(parent_pid: int | None = None, detached_mode: bool = False)`로 호환 시그니처 복구
  - 무인자 호출(테스트 경로)에서 `ppid=1`을 detached 상황으로 처리
- `src/sari/services/admin_service.py`
  - broad-except 제거
  - `except (importlib.metadata.InvalidVersion, ValueError)`로 구체화
- `tests/unit/test_mcp_legacy_tool_parity.py`
  - `save_snippet/get_snippet`는 MCP hidden 정책 기준(`-32601`)으로 기대치 정렬
- `tests/integration/test_lifecycle_e2e.py`
  - orphan self-terminate 검증을 non-detached 직접 실행 경로로 조정
  - 종료 사유 검증을 `get_latest_exit_event()` 기준으로 변경

## 검증
- 타깃 4건:
  - `python3 -m pytest -q tests/unit/test_daemon_process_admin_injection.py::test_is_parent_alive_treats_detached_ppid_as_alive tests/unit/test_mcp_legacy_tool_parity.py::test_save_snippet_and_get_snippet_are_hidden_on_mcp tests/unit/test_no_silent_exception_policy.py::test_no_broad_except_in_sari tests/integration/test_lifecycle_e2e.py::test_orphan_daemon_self_terminates_and_records_exit_reason`
  - 결과: `4 passed`
- 전체(분할 실행):
  - `python3 -m pytest -q -k 'not lifecycle_e2e'` → `287 passed, 2 skipped, 5 deselected`
  - `python3 -m pytest -vv tests/integration/test_lifecycle_e2e.py` → `5 passed`
- release gate:
  - `tools/ci/run_release_gate.sh` → `passed`
