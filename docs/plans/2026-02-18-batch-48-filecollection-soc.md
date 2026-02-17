# Batch-48 FileCollection SoC Tightening

## 목표
- `FileCollectionService`의 위임 경계를 테스트로 고정하고, 미사용 내부 프록시를 정리해 Facade 응집도를 높인다.
- 외부 계약(MCP/HTTP/DB 스키마/CLI 출력)은 변경하지 않는다.

## 구현 내용
- `tests/unit/test_file_collection_soc_delegation.py`
  - watcher 위임 경로(`_handle_fs_event`) 검증 추가
  - metrics 위임 경로(`get_pipeline_metrics`, `_record_enrich_latency`) 검증 추가
- `src/sari/services/file_collection_service.py`
  - 미사용 내부 프록시 메서드 제거
    - `_process_enrich_jobs_impl`
    - `_process_enrich_jobs_l2_impl`
    - `_process_enrich_jobs_l3_impl`
    - `_acquire_l3_jobs`
    - `_resolve_bootstrap_policy`
    - `_compute_coverage_bps`
    - `_refresh_indexing_mode`
    - `_process_enrich_jobs_bootstrap`
    - `_flush_enrich_buffers`
    - `_set_observer`
    - `_handle_background_collection_error`
  - 사용되지 않는 `Observer` 필드/임포트 제거

## 검증 결과
- 타깃 테스트:
  - `python3 -m pytest -q tests/unit/test_file_collection_soc_delegation.py`
  - 결과: `2 passed`
- 영향 테스트:
  - `python3 -m pytest -q tests/unit/test_file_collection_soc_delegation.py tests/unit/test_batch17_performance_hardening.py::test_file_collection_rebalance_jobs_by_language_round_robin tests/unit/test_file_collection_orphan_guard.py`
  - 결과: `4 passed`
  - `python3 -m pytest -q tests/unit/test_exception_policy_followup.py tests/unit/test_pipeline_metrics_eta.py`
  - 결과: `7 passed`
- 전체 테스트:
  - `python3 -m pytest -q`
  - 결과: `234 passed, 2 skipped`
- 품질 게이트:
  - `python3 tools/quality/full_tree_policy_check.py --root src --fail-on-todo`
  - 결과: `total_violations=0`

## 결론
- FileCollection Facade가 외부 인터페이스/조립 책임에 더 집중되도록 정리됐다.
- 기존 동작/정책을 유지한 상태에서 SoC 경계를 테스트로 고정했다.
