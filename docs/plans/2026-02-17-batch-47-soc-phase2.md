# Batch-47 SoC Phase-2 Implementation Log

## 목표
- 외부 계약(MCP/HTTP/DB)을 바꾸지 않고 내부 관심사 분리를 강화한다.
- Batch-46의 라인수 중심 파편화 흔적을 책임 경계 기준으로 정리한다.

## 구현 범위
- HTTP 파싱 책임 분리
  - `src/sari/http/request_parsers.py` 추가
  - `src/sari/http/response_builders.py` 추가
  - `src/sari/http/app.py`에서 파서/응답 빌더 헬퍼 제거 및 모듈 호출로 치환
- HTTP 컨텍스트/관리 엔드포인트 경계 유지
  - `src/sari/http/context.py` 유지
  - `src/sari/http/admin_endpoints.py` 유지
  - `src/sari/http/pipeline_error_endpoints.py` 책임 분리 유지
- Search 결합 책임 분리 유지
  - `src/sari/search/score_blender.py` 사용
  - `src/sari/search/orchestrator.py`는 파이프라인 제어 중심
- LSP 타입 계층 책임 재노출 유지
  - `src/solidlsp/lsp_protocol_handler/lsp_types.py`
  - `src/solidlsp/lsp_protocol_handler/lsp_types_base.py`
  - `src/solidlsp/lsp_protocol_handler/lsp_types_protocol.py`
  - `src/solidlsp/lsp_protocol_handler/lsp_types_capabilities.py`

## 검증 결과
- 선택 회귀 테스트:
  - `pytest -q tests/unit/test_http_read_endpoints.py tests/unit/test_http_pipeline_quality_endpoints.py tests/unit/test_search_rrf_policy.py tests/unit/test_search_importance_and_vector.py tests/unit/test_search_placeholder.py`
  - 결과: `19 passed`
- 전수 품질 게이트:
  - `python3 tools/quality/full_tree_policy_check.py --root src --fail-on-todo`
  - 결과: `total_violations=0`
- 전체 테스트:
  - `pytest -q`
  - 결과: `211 passed, 1 skipped`

## 결론
- 외부 계약 변경 없이 HTTP/Search/LSP 타입 계층의 책임 경계를 강화했다.
- 파싱/응답 변환 로직이 `app.py`에서 분리되어 라우팅/오케스트레이션 응집도가 향상됐다.
