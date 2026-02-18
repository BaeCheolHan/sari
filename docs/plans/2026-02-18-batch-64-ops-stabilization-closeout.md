# Batch-64 Ops Stabilization Closeout

## 목표
- MCP 응답 계약(version/schemaVersion) 일관성 고정
- daemon forward 오류코드 표준화 유지
- doctor 진단 항목(orm_backend/version_alignment)으로 운영 점검성 강화
- release gate + gemini/codex MCP 등록/연결 확인

## 변경 내용
- `src/sari/mcp/server.py`
  - `initialize`/`sari/identify`/`tools/list`에 `schemaVersion`, `schema_version` 포함
  - `serverInfo.version`, `identify.version`을 `sari.__version__` 기반으로 반환
  - daemon forward `tools/list` 경로에서도 schema version 보강
- `src/sari/services/admin_service.py`
  - `doctor` 항목에 `orm_backend`, `version_alignment` 추가
  - 런타임 버전과 설치 메타데이터 버전 불일치 탐지
- `tests/unit/test_mcp_server_protocol.py`
  - 신규 MCP 메타 필드 및 버전 검증 추가

## 검증
- 단위 테스트
  - `pytest -q tests/unit/test_mcp_server_protocol.py tests/unit/test_mcp_daemon_forward.py tests/unit/test_mcp_admin_tools.py`
  - 결과: `15 passed`
- release gate
  - `tools/ci/run_release_gate.sh`
  - 결과: `[release gate] passed`
- CLI 연동
  - `sari install --host gemini`
  - `sari install --host codex`
  - `gemini mcp list`에서 `sari ... Connected` 확인
  - `codex mcp list`에서 `sari enabled` 확인

## 결과
- Batch-64 범위(운영 안정화 마감)는 완료.
- asyncio 전면 전환은 후속 배치로 분리 유지.
