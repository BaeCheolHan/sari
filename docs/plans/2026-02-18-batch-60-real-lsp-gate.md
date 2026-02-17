# Batch-60 Real LSP E2E Full Gate Operationalization

## 목표
- Real LSP E2E 게이트를 PR(report-only) / main(schedule 포함, hard-fail) 2단계 정책으로 고정한다.
- 미설치 서버 감지 시 자동 복구 후 1회 재실행하는 표준 흐름을 CI에 내장한다.
- 게이트 실행 메타(run_id, repair 적용 여부, 재실행 횟수, 최종 판정)를 아티팩트로 남긴다.

## 구현 범위
- `tools/ci/run_lsp_matrix_gate.sh`
  - 자동 복구(`tools/lsp/repair_missing_servers.sh --apply`) 연동
  - 1회 재실행 지원
  - 게이트 요약 아티팩트(`lsp-matrix-gate-summary.json`) 생성
- `.github/workflows/lsp-matrix-pr-gate.yml`
  - trigger 확장: `pull_request` + `push(main)` + `schedule`
  - 모드 분기: PR은 report-only, 나머지는 hard
  - summary 아티팩트 업로드 추가
- `src/sari/services/lsp_matrix_diagnose_service.py`
  - gate run 메타(`gate_mode`, `repair_applied`, `rerun_count`, `final_gate_decision`) 진단 결과/Markdown에 반영
- 문서 갱신
  - `docs/lsp_real_e2e_runbook.md`

## 테스트(TDD)
- RED
  - `tests/unit/test_ci_lsp_matrix_report_only.py`
    - 복구 스크립트/summary 아티팩트/워크플로우 trigger 검증 추가
  - `tests/unit/test_lsp_matrix_diagnose_service.py`
    - gate run 메타 포함 검증 추가
- GREEN
  - 스크립트/워크플로우/서비스 수정
  - 관련 테스트 통과

## 검증 결과
- `python3 -m pytest -q tests/unit/test_ci_lsp_matrix_report_only.py` => 2 passed
- `python3 -m pytest -q tests/unit/test_lsp_matrix_diagnose_service.py` => 2 passed
- `python3 -m pytest -q tests/unit/test_ci_lsp_matrix_report_only.py tests/unit/test_lsp_matrix_diagnose_service.py tests/unit/test_cli_pipeline_lsp_matrix_diagnose.py` => 5 passed
- `python3 -m pytest -q tests/unit/test_pipeline_lsp_matrix_service.py tests/unit/test_cli_pipeline_lsp_matrix_commands.py tests/unit/test_mcp_pipeline_lsp_matrix_tools.py tests/unit/test_http_pipeline_lsp_matrix_endpoints.py` => 8 passed

## 완료 기준
- CI 게이트 스크립트가 복구/재실행/요약 아티팩트를 생성한다.
- PR/report-only와 main(schedule)/hard 모드 분기가 워크플로우에 고정된다.
- 진단 서비스가 gate run 메타를 포함해 보고한다.
