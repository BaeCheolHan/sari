# Batch-63 SQLAlchemy Cutover Phase-2

## 목표
- 남아 있던 repository의 `schema.connect` 직접 의존을 제거한다.
- doctor `orm_backend`를 `sqlalchemy_only` 상태로 수렴한다.

## 구현 체크리스트
- [x] 잔여 16개 repository를 세션 기반 `_dbapi_conn` 어댑터로 전환
- [x] `from sari.db.schema import connect` 제거(전체 repository 0건)
- [x] `with connect(self._db_path)` 제거(전체 repository 0건)
- [x] 전환 후 repository/service/integration 회귀 테스트 통과
- [x] release gate 통과

## 결과
- repository `connect` 직접 의존: `16 -> 0`
- doctor 출력: `sqlalchemy_only`

## 검증
- `PYTHONPATH=src python3 -m pytest -q tests/unit/test_daemon_registry_repository.py tests/unit/test_file_body_repository.py tests/unit/test_pipeline_benchmark_repository.py tests/unit/test_pipeline_quality_repository.py tests/unit/test_pipeline_policy_repository.py tests/unit/test_pipeline_error_event_repository.py tests/unit/test_pipeline_lsp_matrix_repository.py tests/unit/test_symbol_cache_policy.py tests/unit/test_pipeline_control_service.py tests/unit/test_cli_pipeline_benchmark_commands.py tests/unit/test_cli_pipeline_quality_commands.py tests/unit/test_mcp_server_protocol.py tests/integration/test_daemon_http_integration.py tests/unit/test_batch17_performance_hardening.py tests/unit/test_candidate_index_change_repository.py`
  - 결과: `53 passed`
- `tools/ci/run_release_gate.sh`
  - 결과: `passed`
