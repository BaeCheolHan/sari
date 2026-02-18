# Batch-66 Release Stability Gate

## 목표
- CI 실패 시 `mcp_handshake`/`mcp_concurrency` 원인을 summary JSON에서 즉시 확인 가능하도록 한다.
- MCP probe 실행 경로의 프로세스 정리/오류 보고를 안정화한다.
- 로컬/CI에서 동일한 release gate 관찰성(observability)을 확보한다.

## 변경 내용
- `tools/ci/release_gate_mcp_probe.py`
  - `PROBE_SUMMARY:` 접두 JSON 요약 출력 추가
  - handshake/concurrency 실패 시 단계(`stage`)와 stderr tail 포함
  - probe 종료 시 `terminate -> wait -> kill` 정리 경로 추가
  - `pkill` 실행을 `shell=False` 리스트 인자로 전환
  - UTF-8 디코드 `errors="ignore"` 제거 (침묵 파싱 금지)
- `tools/ci/run_release_gate.sh`
  - summary 생성 단계에 `probe_details` 필드 추가
  - handshake/concurrency 로그에서 `PROBE_SUMMARY` 추출해 JSON에 반영

## 테스트
- `tests/unit/test_ci_release_gate_mcp_probe.py` 신규
  - probe 스크립트가 `PROBE_SUMMARY`를 출력하는 계약
  - `shell=True` 미사용 계약
  - release gate summary에 `probe_details` 포함 계약

## 검증
- 단위 테스트 통과
- `tools/ci/run_release_gate.sh` 실행 통과 및 `artifacts/ci/release-gate-summary.json`의 `probe_details` 확인
