# Batch-65 Security Boundary Hardening

## 목표
- LSP 프로세스 실행 경계에서 `shell=True` 제거
- MCP 직렬화 경계에서 비정상 유니코드(고립 surrogate) 방어
- repo 상대 경로 정규화 시 경로 이탈(`..`) 및 절대경로 차단

## 변경 내용
- `src/solidlsp/ls_handler.py`
  - `_normalize_command_args` 추가
  - `Popen(..., shell=True)` 제거, argv 리스트 + `shell=False`로 고정
- `src/solidlsp/language_servers/common.py`
  - `_normalize_command_args` 추가
  - `subprocess.run(..., shell=True)` 제거, argv 리스트 + `shell=False`로 고정
- `src/sari/mcp/transport.py`
  - `_sanitize_json_value`, `_sanitize_text` 추가
  - `write_message` 직렬화 전에 텍스트 경계 정제 수행
- `src/sari/lsp/path_normalizer.py`
  - `normalize_repo_relative_path`에 절대경로/상위경로 이동 검증 추가

## 테스트
- `tests/unit/test_solidlsp_subprocess_security.py` 신규
  - 명령 분해/빈 커맨드 차단 검증
- `tests/unit/test_mcp_stdio_framed_transport.py`
  - 고립 surrogate 직렬화 방어 검증 추가
- `tests/unit/test_lsp_path_normalizer.py`
  - `..`/절대경로 거부 검증 추가

## 검증 결과
- `pytest -q tests/unit/test_lsp_path_normalizer.py tests/unit/test_mcp_stdio_framed_transport.py tests/unit/test_solidlsp_subprocess_security.py`
  - `11 passed`
- `pytest -q tests/unit/test_mcp_server_protocol.py tests/unit/test_lsp_hub_mapping.py`
  - `25 passed`
