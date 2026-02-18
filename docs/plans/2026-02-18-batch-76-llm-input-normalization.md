# Batch-76 LLM Input Normalization & Self-Describing Errors

## 목표
- LLM이 canonical 파라미터를 기억하지 못해도 도구를 직관적으로 호출할 수 있도록 인자 정규화를 도입한다.
- 실패 응답에 `expected/received/example` 힌트를 포함해 재시도 비용을 줄인다.
- PACK 라인 오류 응답에 `@HINT`를 추가해 텍스트만으로도 수정 가능한 정보를 제공한다.

## 구현
- 신규: `src/sari/mcp/tools/arg_normalizer.py`
  - `NormalizedArgumentsDTO`, `ArgAliasRuleDTO` 추가
  - 도구별 alias 정규화 규칙 추가
    - `read`: `path/file_path/relative_path -> target`, `file_preview -> file`
    - `search`: `q/keyword -> query`
    - `read_symbol/get_callers/get_implementations/call_graph`: `symbol_id/sid/name/target -> symbol`
    - `search_symbol`: `path -> path_prefix`, `q/keyword -> query`
    - `knowledge/get_context/get_snippet/list_symbols`: `q/keyword -> query`
  - 내부 메타(`__sari_arg_meta`)로 `received_keys`, `normalized_from` 전달

- 수정: `src/sari/mcp/server.py`
  - `tools/call` 경계에서 공통 인자 정규화 수행 후 handler 호출

- 수정: `src/sari/mcp/tools/pack1.py`
  - `pack1_error`에 `expected`, `received`, `example`, `normalized_from` 확장

- 수정: `src/sari/mcp/pack1_line.py`
  - 오류 응답 시 `@HINT expected=... received=... example=...` 라인 출력

- 수정: `src/sari/mcp/tools/legacy_tools.py`
  - read 인자 오류를 자기설명형(`expected/example`)으로 반환
  - `ERR_MODE_REQUIRED`, `ERR_UNSUPPORTED_MODE`, `ERR_TARGET_REQUIRED`에 힌트 적용

- 수정: `src/sari/mcp/tool_visibility.py`
  - `tools/list` 응답(프록시/stdio 경유)에 `x_examples` 메타 주입

## 테스트
- 신규: `tests/unit/test_arg_normalizer.py`
  - read mode/path alias 정규화
  - canonical 우선순위 검증
- 수정: `tests/unit/test_mcp_stabilization_read.py`
  - `read(mode=file_preview, path=...)` 성공 검증
  - `read(file)` target 누락 시 `expected/example` 및 `@HINT` 검증

## 검증
- `python3 -m pytest -q tests/unit/test_arg_normalizer.py tests/unit/test_mcp_stabilization_read.py::test_read_normalizes_mode_and_path_alias tests/unit/test_mcp_stabilization_read.py::test_read_missing_target_returns_self_describing_error`
  - `4 passed`
- `python3 -m pytest -q tests/unit/test_mcp_* tests/unit/test_pack1_line.py tests/unit/test_arg_normalizer.py tests/unit/test_daemon_resolver_and_proxy.py tests/unit/test_ci_release_gate_mcp_probe.py`
  - `77 passed`
- `python3 -m pytest -q tests/integration/test_daemon_http_integration.py`
  - `2 passed`
- `tools/ci/run_release_gate.sh`
  - `[release gate] passed`
