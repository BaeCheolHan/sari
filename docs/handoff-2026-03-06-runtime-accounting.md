# SARI Handoff - 2026-03-06

## 목표
- `sari mcp` / daemon / LSP 수집 경로를 안정화한다.
- 특히 `call_graph`가 비는 근본 원인인 `LSP soft-limit`, `probe state drift`, `runtime 상태 불투명성`을 줄인다.
- 최종적으로는:
  - 왜 막히는지 `status`/`doctor`에서 보이고
  - manual 요청이 background fan-out에 묻히지 않으며
  - 향후 selective eviction을 안전하게 재도입할 수 있는 계측 기반을 만든다.

## 지금까지 한 작업

### 1. 이미 반영된 큰 축
- `repo_id` 무결성 불일치 수정
  - fresh DB에서도 재현되던 SSOT 불일치를 수정했다.
  - 현재 `candidate_index_changes`, `file_enrich_queue`, `lsp_symbols`, `lsp_call_relations`의 `repo_id` 무결성 오류는 0건 기준으로 재검증했다.

- MCP degraded startup 경로 추가
  - `ERR_REPO_ID_INTEGRITY`가 있어도 `stdio handshake` 자체는 깨지지 않게 수정했다.
  - raw JSON이 `stdout`을 오염시키지 않도록 정리했다.

- `repo_language_probe_state` 영속화
  - `repo_root + language` 단위로 probe 상태를 DB에 저장한다.
  - `COOLDOWN`, `UNAVAILABLE_COOLDOWN`, `WORKSPACE_MISMATCH`, `BACKPRESSURE_COOLDOWN` 등의 상태와 `last_error_code`, `next_retry_at`, `last_trigger`를 볼 수 있게 만들었다.

- `status` / `doctor` 가시성 보강
  - `repo_language_probe`가 노출되며, `BACKPRESSURE_COOLDOWN`과 manual/backpressure 신호를 분리해서 볼 수 있다.
  - cold repo manual backpressure도 `doctor`에서 놓치지 않게 수정했다.

### 2. 시도했다가 철회한 축
- `manual starvation`을 완화하려고 soft-limit 하에서 priority eviction을 넣었다.
- 하지만 `LspHub`가 runtime이 truly idle인지 모르는 상태에서 eviction을 수행하면 in-flight 요청을 깨뜨릴 수 있어 리뷰에서 계속 정확한 회귀가 나왔다.
- 현재 결론:
  - `manual hot` / `manual intent` / `manual backpressure` 관측은 유지
  - cross-repo 또는 same-repo runtime eviction은 제거
  - selective eviction 재도입은 `busy/idle` 계측이 들어간 뒤 별도 단계로 미룬 상태

### 3. 이번 세션에서 한 작업
- `runtime busy/idle accounting` 선행 작업 일부 구현
  - `src/solidlsp/ls_handler.py`
    - request lifecycle hook 뼈대 존재 확인
    - `send_request`, `send_batch_requests`, `_cancel_pending_requests`에서 start/end hook이 호출되는 상태 확인
  - `src/solidlsp/ls.py`
    - `SolidLanguageServer.set_request_lifecycle_hooks(...)` 위임 메서드 추가
  - `src/sari/lsp/hub.py`
    - `LspRuntimeEntry`에 다음 필드 추가
      - `active_request_count`
      - `last_request_started_at`
      - `last_request_finished_at`
      - `last_request_kind`
      - `last_request_method`
    - `record_request_start(...)`, `record_request_end(...)` 추가
    - `get_repo_runtime_activity(...)` 추가
    - `get_metrics()`에 다음 메트릭 추가
      - `lsp_active_request_count`
      - `lsp_busy_runtime_count`
      - `lsp_idle_runtime_count`
      - `lsp_runtime_activity_anomaly_count`
    - `idle eviction`과 `max_instances LRU eviction`이 busy runtime을 건드리지 않도록 보강
    - cleanup/stop 시 `active_request_count > 0`이면 anomaly로 집계
    - 새 runtime 시작 후 lifecycle hook을 주입하되, legacy fake server 호환을 위해 setter가 있을 때만 호출하도록 처리
  - `src/sari/mcp/tools/status_tool.py`
    - `repo_runtime_activity_provider` 추가
    - `runtime_activity` payload 추가
  - `src/sari/mcp/server.py`
    - `StatusTool`에 `shared_hub.get_repo_runtime_activity` wiring 추가

## 현재 워킹트리 상태
- 커밋되지 않은 변경이 남아 있다.
- 대표 파일:
  - `src/sari/lsp/hub.py`
  - `src/sari/mcp/server.py`
  - `src/sari/mcp/tools/status_tool.py`
  - `src/solidlsp/ls.py`
  - `src/solidlsp/ls_handler.py`
  - 여러 probe/backpressure 관련 파일
  - 신규 테스트: `tests/unit/lsp/test_solidlsp_request_lifecycle.py`

## 이번 세션에서 확인한 테스트

### 통과
- `uv run pytest -q tests/unit/lsp/test_solidlsp_request_lifecycle.py tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_busy_runtime_is_not_idle_evicted tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_runtime_activity_metrics_follow_request_lifecycle tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_cleanup_busy_runtime_counts_activity_anomaly tests/unit/mcp/test_status_language_support_contract.py::test_mcp_status_exposes_runtime_activity_snapshot`
  - `6 passed`

- `uv run pytest -q tests/unit/lsp/test_solidlsp_request_lifecycle.py tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_evicts_idle_instances tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_evicts_lru_when_max_instances_exceeded tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_busy_runtime_is_not_idle_evicted tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_runtime_activity_metrics_follow_request_lifecycle tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_cleanup_busy_runtime_counts_activity_anomaly tests/unit/mcp/test_status_language_support_contract.py::test_mcp_status_exposes_language_readiness_snapshot tests/unit/mcp/test_status_language_support_contract.py::test_mcp_status_priority_class_uses_row_last_trigger tests/unit/mcp/test_status_language_support_contract.py::test_mcp_status_exposes_runtime_activity_snapshot`
  - `10 passed`

### 주의
- `tests/unit/lsp/test_solidlsp_request_lifecycle.py tests/unit/lsp/test_lsp_hub_mapping.py tests/unit/mcp/test_status_language_support_contract.py` 전체 파일 묶음은 이 환경에서 종료 없이 남아 `pkill`로 정리했다.
- 원인은 아직 분석하지 않았다. 특정 기존 테스트의 hang 가능성이 있다.

## 아직 안 끝난 것

### A. runtime accounting 1차 구현은 끝나지 않았다
- 아직 `busy/idle` 계측은 `status` tool 수준까지만 연결했다.
- 다음이 남아 있다.
  - daemon/http 계층에서 같은 activity를 볼 필요가 있는지 결정
  - `doctor`에 runtime activity anomaly check 추가
  - 실제 runtime create/reuse 경로 전반에서 hook이 빠지는 곳이 없는지 점검

### B. selective eviction은 아직 금지 상태
- `_try_free_soft_limit_capacity_locked()`는 계속 `False`
- 의도적으로 비활성화 상태다.
- 이건 미완료가 아니라 현재 안전선이다.

### C. 원래 문제는 아직 완전히 해결되지 않았다
- `manual starvation`의 “관측”은 좋아졌지만
- `manual 요청이 soft-limit에서 실제 더 잘 통과한다`는 행동 개선은 아직 없다
- 그건 `busy/idle` 계측이 충분히 검증된 뒤 다음 단계에서 다시 설계해야 한다

## 이어서 해야 할 작업

### 1순위
- runtime accounting Phase 마무리
  - `doctor`에 `runtime activity visible / anomaly` 체크 추가
  - busy runtime은 idle cleanup 뿐 아니라 다른 cleanup 경로에서도 제거되지 않는지 점검
  - 필요 시 `status`에 repo별 `busy/idle`만이 아니라 runtime 개별 스냅샷이 필요한지 검토

### 2순위
- `hub` 전반 회귀 검증 확대
  - 현재는 추가한 지점 위주로만 통과 확인
  - 기존 `test_lsp_hub_mapping.py` 전체에서 hang/회귀가 없는지 다시 좁혀서 점검해야 한다

### 3순위
- 그 다음에만 selective eviction 재설계
  - 전제:
    - `active_request_count == 0`
    - starting 아님
    - retention 없음
    - idle grace 지난 runtime만 후보
  - 이 전제가 만족되기 전에는 eviction 재도입 금지

## 다음 작업자가 바로 알아야 할 판단
- 지금까지 문제가 반복된 이유는 단순한 패치 실수가 아니라, `manual intent`, `probe state`, `admission result`, `runtime in-flight state`를 서로 대용으로 써왔기 때문이다.
- 지금 단계에서는 공격적인 최적화보다 상태 경계 분리가 우선이다.
- 특히 `manual starvation`을 해결하려고 eviction을 먼저 넣는 건 다시 같은 리뷰 루프를 만든다.

## 권장 시작점
1. `git diff -- src/sari/lsp/hub.py src/sari/mcp/tools/status_tool.py src/solidlsp/ls.py src/solidlsp/ls_handler.py`
2. 위 문서의 테스트 2개 묶음을 다시 실행
3. hang 나는 전체 묶음은 작은 그룹으로 쪼개 원인 테스트를 찾기
4. 그 다음 `doctor` / runtime anomaly 노출 보강으로 진행
