# Batch-73 PACK1 LLM Friendliness Deepening

## 목표
- PACK1 v2 라인 응답의 도구군별 계약을 명시적으로 고정한다.
- 검색/심볼/읽기 계열은 strict 계약 위반 시 명시 에러를 반환한다.
- 운영/관리 계열은 record fallback으로 LLM 해석 가능성을 높이고 오탐 오류를 줄인다.

## 구현 내용
- `src/sari/mcp/pack1_line.py`
  - `STRICT_CONTRACT_TOOLS` 추가:
    - `search`, `read`, `read_file`, `list_symbols`, `read_symbol`, `search_symbol`, `get_callers`, `get_implementations`, `call_graph`
  - `RECORD_FALLBACK_TOOLS` 추가:
    - `status`, `doctor`, `rescan`, `repo_candidates`, `scan_once`, `list_files`, `index_file`
  - `pipeline_*` 도구는 record fallback 대상으로 처리.
  - strict 계약에서만 `rid/path/sk/score/src` 검증 에러를 활성화.
  - record fallback 도구에서 `kind=file`을 `kind=record`로 정규화.

- `tests/unit/test_pack1_line.py`
  - `repo_candidates` 렌더링 시 `kind=record` 검증 추가.
  - `search_symbol` strict 계약 검증(잘못된 score 입력 시 `ERR_PACK_CONTRACT_VIOLATION`) 추가.

## 검증
- `python3 -m pytest -q tests/unit/test_pack1_line.py`
  - 결과: `5 passed`
- `python3 -m pytest -q tests/unit/test_mcp_* tests/unit/test_ci_release_gate_mcp_probe.py`
  - 결과: `51 passed`
- `python3 -m pytest -q tests/integration/test_daemon_http_integration.py`
  - 결과: `2 passed`
- `tools/ci/run_release_gate.sh`
  - 결과: `[release gate] passed`

## 결과
- strict 도구군은 계약 위반을 즉시 에러로 노출한다.
- 운영/관리 도구군은 안정적으로 PACK 라인 출력(`@R kind=record ...`)을 제공한다.
- MCP handshake/concurrency를 포함한 release gate가 통과했다.
