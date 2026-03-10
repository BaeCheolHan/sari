# Runtime Activity Stabilization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `LspHub`가 runtime의 in-flight/busy 상태를 정확히 추적하고, 그 상태를 `status`/`doctor`와 테스트에서 신뢰할 수 있게 만든다.

**Architecture:** 이번 단계는 selective eviction을 구현하지 않는다. 대신 `solidlsp -> hub -> status/doctor`로 이어지는 request lifecycle 계측을 완성하고, busy runtime이 cleanup/eviction에서 안전하게 보호되는지 먼저 증명한다. hang가 나는 테스트는 별도 원인 분리 루프로 좁혀서, 검증 기반이 불안정한 상태에서 추가 구조 변경이 들어가지 않게 한다.

**Tech Stack:** Python, pytest, SQLite-backed SARI runtime services, solidlsp request handler hooks

---

### Task 1: Runtime Activity 계측 경계 고정

**Files:**
- Modify: `src/solidlsp/ls_handler.py`
- Modify: `src/solidlsp/ls.py`
- Modify: `src/sari/lsp/hub.py`
- Test: `tests/unit/lsp/test_solidlsp_request_lifecycle.py`
- Test: `tests/unit/lsp/test_lsp_hub_mapping.py`

**Step 1: failing test를 추가/보강한다**

추가/유지해야 할 케이스:
- request success에서 start/end가 정확히 1회씩 호출된다.
- `_cancel_pending_requests()`에서 end hook이 중복 없이 1회 호출된다.
- busy runtime은 idle eviction 대상이 아니다.
- cleanup 시 `active_request_count > 0`이면 anomaly metric이 증가한다.

**Step 2: failing test만 실행해 RED를 확인한다**

Run:
```bash
uv run pytest -q \
  tests/unit/lsp/test_solidlsp_request_lifecycle.py \
  tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_busy_runtime_is_not_idle_evicted \
  tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_runtime_activity_metrics_follow_request_lifecycle \
  tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_cleanup_busy_runtime_counts_activity_anomaly
```

Expected:
- 초기에는 인터페이스 부재 또는 metric mismatch로 FAIL

**Step 3: 최소 구현으로 lifecycle 계측을 연결한다**

구현 범위:
- `SolidLanguageServer.set_request_lifecycle_hooks(...)` 추가
- `LspRuntimeEntry`에 activity 필드 추가
- `LspHub.record_request_start/end(...)` 추가
- `get_metrics()`에 activity metric 추가
- `busy runtime`은 `_evict_idle_locked()` / `_evict_lru_if_needed_locked()`에서 제거하지 않음
- `_cleanup_not_running_entry_locked()` / `_stop_entry_locked()`에서 active request 누수는 anomaly로 기록

**Step 4: RED였던 테스트를 다시 실행해 GREEN 확인**

Run:
```bash
uv run pytest -q \
  tests/unit/lsp/test_solidlsp_request_lifecycle.py \
  tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_busy_runtime_is_not_idle_evicted \
  tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_runtime_activity_metrics_follow_request_lifecycle \
  tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_cleanup_busy_runtime_counts_activity_anomaly
```

Expected:
- PASS

### Task 2: Status/Doctor 가시성 마무리

**Files:**
- Modify: `src/sari/mcp/tools/status_tool.py`
- Modify: `src/sari/mcp/tools/admin_tools.py`
- Modify: `src/sari/mcp/server.py`
- Test: `tests/unit/mcp/test_status_language_support_contract.py`
- Test: `tests/unit/mcp/test_mcp_admin_tools.py`

**Step 1: failing test를 추가/보강한다**

추가/유지해야 할 케이스:
- `status`가 `runtime_activity`를 반환한다.
- `doctor`가 runtime activity anomaly를 감지할 수 있다면 새 check를 노출한다.
- 기존 `repo_language_probe` / `manual starvation` 의미는 회귀하지 않는다.

**Step 2: targeted test로 RED를 확인한다**

Run:
```bash
uv run pytest -q \
  tests/unit/mcp/test_status_language_support_contract.py::test_mcp_status_exposes_runtime_activity_snapshot \
  tests/unit/mcp/test_mcp_admin_tools.py -k runtime
```

Expected:
- 새 payload/check가 없으면 FAIL

**Step 3: 최소 구현을 추가한다**

구현 범위:
- `StatusTool`에 `repo_runtime_activity_provider` 주입
- `runtime_activity` payload 추가
- 필요 시 `DoctorTool`에 `lsp_runtime_activity_visible` 또는 anomaly check 추가
- `Server` wiring 반영

**Step 4: 관련 테스트를 실행해 GREEN 확인**

Run:
```bash
uv run pytest -q \
  tests/unit/mcp/test_status_language_support_contract.py \
  tests/unit/mcp/test_mcp_admin_tools.py -k 'runtime or starvation'
```

Expected:
- PASS

### Task 3: Hang 테스트 원인 분리

**Files:**
- Modify: `tests/unit/lsp/test_lsp_hub_mapping.py` (필요 시 marker/log helper만)
- Modify: `tests/unit/mcp/test_status_language_support_contract.py` (필요 시 marker/log helper만)
- Optional: `docs/handoff-2026-03-06-runtime-accounting.md`
- Optional: 새 조사 메모 `docs/plans/2026-03-10-hang-isolation-notes.md`

**Step 1: 재현 범위를 최소 단위로 분리한다**

Run examples:
```bash
uv run pytest -q tests/unit/lsp/test_lsp_hub_mapping.py
uv run pytest -q tests/unit/mcp/test_status_language_support_contract.py
uv run pytest -q tests/unit/lsp/test_solidlsp_request_lifecycle.py tests/unit/lsp/test_lsp_hub_mapping.py tests/unit/mcp/test_status_language_support_contract.py
```

Expected:
- 어느 묶음에서 종료 없이 남는지 재현 여부 기록

**Step 2: bisect 방식으로 hang 유발 테스트를 좁힌다**

방법:
- 파일 단위 -> 테스트 함수 그룹 단위 -> 단일 테스트 단위로 줄인다.
- `-k`를 사용해 절반씩 나누고, hang 재현 여부를 기록한다.

**Step 3: root cause 가설을 하나로 고정한다**

예상 후보:
- cleanup thread와 fake server/clock 조합
- background daemon thread 미정리
- pytest fixture/monkeypatch 누수
- pending request 훅과 long-lived thread 상호작용

**Step 4: 재현 케이스에만 failing test 또는 조사 메모를 남긴다**

원칙:
- 이 Task에서는 추측성 수정 금지
- root cause를 재현하는 최소 케이스를 먼저 만든다

### Task 4: Busy Runtime 계약 회귀 방지

**Files:**
- Modify: `src/sari/lsp/hub.py`
- Test: `tests/unit/lsp/test_lsp_hub_mapping.py`
- Test: 필요 시 새 파일 `tests/unit/lsp/test_lsp_runtime_activity_contract.py`

**Step 1: busy runtime 관련 기존 cleanup 경로를 전수 확인한다**

대상:
- `_evict_idle_locked()`
- `_evict_lru_if_needed_locked()`
- `_cleanup_not_running_entry_locked()`
- `stop_all()`
- `restart_if_unhealthy()`
- `force_restart()`

**Step 2: busy runtime이 의도치 않게 제거되지 않는다는 failing test를 추가한다**

예시:
- `force_restart`는 명시적 운영 명령이므로 busy 보호 대상에서 제외할지 결정 필요
- `idle cleanup`과 `capacity cleanup`은 busy runtime 보호 대상

**Step 3: 구현/주석을 계약에 맞게 정리한다**

원칙:
- “busy runtime은 자동 cleanup/eviction 대상이 아님”을 코드 주석과 테스트 이름으로 고정
- 명시적 운영 명령은 예외일 수 있으나, 그 의미를 문서화

**Step 4: 관련 테스트를 실행한다**

Run:
```bash
uv run pytest -q tests/unit/lsp/test_lsp_hub_mapping.py -k 'busy or evict or cleanup or restart'
```

Expected:
- PASS

### Task 5: Selective Eviction 재도입 여부를 평가만 한다

**Files:**
- Update: `docs/handoff-2026-03-06-runtime-accounting.md`
- Optional: `docs/plans/2026-03-10-selective-eviction-design.md`

**Step 1: 이번 단계 종료 조건을 확인한다**

필수 조건:
- busy/idle metrics가 status에서 보인다.
- anomaly metric이 동작한다.
- hang 원인이 최소 1개 재현 케이스로 좁혀졌다.
- busy runtime 자동 eviction이 없다는 테스트가 있다.

**Step 2: selective eviction 재도입 여부를 결정한다**

결정 규칙:
- 위 조건이 하나라도 불충족이면 재도입 금지
- 모두 충족되면 다음 단계 설계 문서로만 넘긴다

**Step 3: handoff 문서를 갱신한다**

갱신 내용:
- 무엇이 끝났는지
- 무엇이 아직 미완료인지
- eviction을 다시 논의해도 되는지 여부

## 최종 검증

Run:
```bash
uv run pytest -q \
  tests/unit/lsp/test_solidlsp_request_lifecycle.py \
  tests/unit/lsp/test_lsp_hub_mapping.py -k 'busy or evict or cleanup or runtime_activity' \
  tests/unit/mcp/test_status_language_support_contract.py \
  tests/unit/mcp/test_mcp_admin_tools.py -k 'runtime or starvation'
```

Expected:
- PASS
- hang 없거나, hang 있으면 재현 범위가 문서화되어 있어야 함

## Notes
- 이번 계획은 `manual starvation`을 직접 해결하지 않는다.
- 이번 단계의 산출물은 “안전한 관측과 계약”이다.
- selective eviction은 후속 설계로만 다룬다.
