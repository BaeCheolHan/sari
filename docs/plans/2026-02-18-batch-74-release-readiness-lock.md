# Batch-74 Release Readiness Lock

## 목표
- `2.0.10` 패치 릴리즈 준비 상태를 코드/테스트/게이트 기준으로 고정한다.
- PACK1 v2 라인 계약 변경분을 포함한 전체 회귀를 재검증한다.
- 로컬 빌드와 CLI 진입 검증으로 배포 전 결함을 사전 차단한다.

## 변경
- 버전 상향
  - `pyproject.toml`: `2.0.9 -> 2.0.10`
  - `src/sari/__init__.py`: `2.0.9 -> 2.0.10`

## 검증 체크리스트
- [x] 단위 테스트: `tests/unit/test_pack1_line.py`
- [x] 단위 테스트: `tests/unit/test_mcp_*`
- [x] 단위 테스트: `tests/unit/test_ci_release_gate_mcp_probe.py`
- [x] 통합 테스트: `tests/integration/test_daemon_http_integration.py`
- [x] 릴리즈 게이트: `tools/ci/run_release_gate.sh`
- [x] 로컬 패키지 빌드: `python3 -m build`
- [x] CLI 기본 진입 확인: `python3 -m sari.cli.main --help`

## 완료 기준
- 위 체크리스트 전 항목 통과
- 릴리즈 게이트 summary가 `passed`
- 빌드 산출물(wheel/sdist) 생성 성공
