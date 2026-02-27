# Pipeline Quality Test Package

이 디렉터리는 L3 추출 결과를 LSP/Golden 기준으로 검증하는 테스트를 모은 패키지다.

- `test_l3_quality_evaluation_service.py`
  - L3(AST)와 LSP 심볼 비교 품질(Recall/Precision Proxy, Kind/Position Match) 검증
- `test_pipeline_quality_service.py`
  - Golden 대비 품질 게이트(precision/recall/error_rate)와 fallback 동작 검증
- `test_cli_pipeline_quality_commands.py`
  - `pipeline quality run/report` CLI 경로 검증
- `test_http_pipeline_quality_endpoints.py`
  - 품질 실행/리포트 HTTP 엔드포인트 검증
- `test_l3_extract_success_stage.py`
  - L3 extract 성공 단계의 quality shadow compare 기록/사유코드 반영 검증

