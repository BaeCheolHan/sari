# L5 비동기 품질 교정 아키텍처 (Async Quality Upgrade)

## 배경 및 문제 정의

### 측정된 현상

bulk indexing 기준 (sari repo, 598개 파일):

| 항목 | 수치 |
|------|------|
| 전체 TPS (개선 전) | ~30 TPS |
| 전체 TPS (현재, `.sh`/`.md` exclude 후) | ~157 TPS |
| L5(LSP) 실제 처리 비율 | ~0.8% (5개 / 598개) |
| tree-sitter(L3)만 처리 | ~99.2% (593개) |

### 구조적 원인

```
현재 파이프라인:
L1 → L2 → L3 ──handoff_to_l5()──> L5 queue ──> process_enrich_jobs_l5()
                                                      │
                                               L5AdmissionRuntimeService
                                               ├─ calls_per_min_per_lang_max (30)
                                               ├─ BATCH 모드 rate limit (5%)
                                               ├─ cooldown 30s on reject
                                               └─ 98.8% reject
                                                      │
                                                  0.8% 만 LSP probe 도달
```

**이중 문제:**
1. L5 queue로 handoff는 되지만 rate limit이 대부분 거부 → cold start overhead만 발생
2. cooldown 30s > 전체 run 3.8s → 한 번 거부되면 재시도 불가

### 현재 코드의 핵심 경로

**L3 → L5 handoff** (`src/sari/services/collection/l3/l3_orchestrator.py:275-278`):
```python
if (
    not skip_requested
    and self._handoff_to_l5 is not None
):
    handoff_changed = bool(self._handoff_to_l5(job, now_iso))
```

**L5 어드미션 enforcement 상태** (`src/sari/services/collection/service.py:269-270`):
```python
l5_admission_shadow_enabled=(self._run_mode == "prod"),
l5_admission_enforced=False,   # ← 초기값. control_service.py:270에서 런타임에 True로 전환 가능
```
> `decision_stage.py:98`: `not admission_decision.admit_l5 and self._admission_enforced` → **enforced=True일 때만 실제 차단**. soft 모드(False)에서는 거부 판정을 기록하나 처리는 계속 진행.
> `control_service.py:270`: `stage_b_passed`가 True이면 런타임에 `enforced=True`로 전환될 수 있음.

**L5 admission policy** (`src/sari/services/collection/l5/l5_default_policy_builder.py:23-27`):
```python
L5RequestMode.BATCH: (
    L5ReasonCode.GOLDENSET_COVERAGE,  # 언어별 policy에서 BATCH 허용
    L5ReasonCode.REGRESSION_SAMPLING,
    L5ReasonCode.UNRESOLVED_SYMBOL,
),
```
> `l5_admission_policy.py:95-97`의 fallback `default_language_policy`(cost_multiplier=8.0)는 BATCH에서 `REGRESSION_SAMPLING`만 허용하지만,
> 실운영에서 사용되는 `build_default_language_policy_map()` 결과(cost_multiplier=1.0)는 BATCH에서 `GOLDENSET_COVERAGE`도 허용.
> → rate limit 자체보다는 **rate limit 초과 시 cooldown 30s** 가 bulk indexing을 막는 핵심 원인.

**cooldown 기간** (`src/sari/services/collection/l5/l5_admission_runtime_service.py:205-211`):
```python
duration_sec_by_reason: dict[L5RejectReason, float] = {
    L5RejectReason.PRESSURE_RATE_EXCEEDED: 30.0,   # 30초 cooldown
    L5RejectReason.PRESSURE_BURST_EXCEEDED: 10.0,
    L5RejectReason.PRESSURE_WORKSPACE_EXCEEDED: 20.0,
    L5RejectReason.COOLDOWN_ACTIVE: 15.0,
}
```

---

## 제안 아키텍처

### 핵심 아이디어

**"빠른 초기 인덱싱(L3) + EventBus 기반 사후 품질 교정(L5 async)"**

```
Phase 1 (Fast path - L1~L3):
  L1 (LSP warm-up kickoff) → L2 → L3 → store
  ├─ L5 handoff 없음 (handoff_to_l5=None)  ← scan/watcher 무관, 모든 출처
  └─ TPS 최대화

                    ↓ EventBus 이벤트

Phase 2 (Async quality upgrade - 신규, EventBus 기반):
  Publishers:
    L3 flush (needs_l5=1 적재)  ──publish→ L3FlushCompleted
    LSP warm 완료 (Wave2 후)   ──publish→ LspWarmReady
    daemon 종료                ──publish→ ShutdownRequested

  Subscriber — L5AsyncUpgradeWatcher:
    subscribe_queue([L3FlushCompleted, LspWarmReady, ShutdownRequested])
    ├─ LspWarmReady 수신 → _activated=True, watcher loop 시작
    ├─ L3FlushCompleted 수신 → 즉시 DB 조회 → needs_l5 파일 batch enqueue
    ├─ timeout (poll_interval) → 주기적 DB 조회 (누락 방지)
    └─ ShutdownRequested 수신 → loop 종료

User-facing:
  request_kind="interactive" pool → Phase 2와 pool 분리
```

### Before / After 데이터 흐름

```
Before:
  L3 완료 ─handoff──> L5 queue ──> 98.8% reject (30s cooldown)
                              └──> 0.8% LSP probe

After:
  Phase 1: L3 완료 ──store──> DB (needs_l5=1)
                          ──publish──> L3FlushCompleted (EventBus)
  Phase 2: L5AsyncUpgradeWatcher
           ├─ LspWarmReady 수신 → 활성화
           ├─ L3FlushCompleted 수신 → 즉시 DB 조회 → L5 enqueue
           └─ 결과: LSP warm + L3 적재 즉시 반응, 100% L5 처리
```

---

## L4 needs_l5 품질 메트릭 계산 (현행 유지 대상)

Phase 2가 올바르게 동작하려면 Phase 1에서 L4 품질 메트릭이 **정확히 DB에 기록**되어야 한다.
이 섹션은 현재 계산 로직을 명세하며, 제안 아키텍처에서도 이 로직은 **변경 없이 유지**된다.

### 1단계: L3PreprocessDecision 결정

**파일:** `src/sari/services/collection/l3/l3_treesitter_preprocess_service.py`

`L3TreeSitterPreprocessService.preprocess()` 가 `L3PreprocessResultDTO`를 반환할 때
`decision` 필드가 세 가지 중 하나로 결정된다.

```python
class L3PreprocessDecision(str, Enum):
    L3_ONLY      = "l3_only"       # tree-sitter로 충분, L5 불필요
    NEEDS_L5     = "needs_l5"      # LSP 보강 필요
    DEFERRED_HEAVY = "deferred_heavy"  # 파일 크기 초과, L5로 위임
```

**`NEEDS_L5`로 결정되는 조건** (line 138~248):

| 조건 | 이유 코드 |
|------|----------|
| `.ts/.tsx/.js/.jsx/.mjs/.cjs` 확장자 (TSLS fast path) | `l3_preprocess_tsls_fast_path` |
| 파일 크기 > `max_bytes` (262KB) | `l3_preprocess_large_file` → `DEFERRED_HEAVY` |
| tree-sitter 미지원 언어 | `l3_preprocess_unsupported_language` |
| tree-sitter degraded 또는 심볼 0개 | `l3_preprocess_no_symbols` |
| import/cross-file 힌트 감지 (`extends`, `implements`, `::`) | `l3_preprocess_low_confidence` |
| regex timeout (query budget 초과) | `l3_query_budget_exceeded` |

**`L3_ONLY`로 결정되는 조건** (line 184~191):
- tree-sitter 성공 + 심볼 > 0 + cross-file 힌트 없음

---

### 2단계: L4 품질 메트릭 계산

**파일:** `src/sari/services/collection/layer_upsert_builder.py:41-87`

L3 처리 완료 후 `LayerUpsertBuilder.build_l4()`가 `preprocess_result.decision` 기반으로
`tool_data_l4_normalized_symbols` 테이블에 저장할 값을 계산한다.

```python
# layer_upsert_builder.py:64-67 (핵심 계산 로직)

# needs_l5: L3_ONLY가 아니면 모두 True
needs_l5 = preprocess_result.decision is not L3PreprocessDecision.L3_ONLY

# confidence: LSP 없이 신뢰할 수 있으면 0.9, 아니면 0.35
confidence = 0.9 if not needs_l5 and not degraded else 0.35

# coverage:
#   DEFERRED_HEAVY → 0.0 (파일 자체를 처리 못함)
#   degraded       → 0.6 (일부 처리)
#   정상            → 1.0
coverage = (
    0.0 if decision is L3PreprocessDecision.DEFERRED_HEAVY
    else (0.6 if degraded else 1.0)
)

# ambiguity: confidence의 보수
ambiguity = max(0.0, min(1.0, 1.0 - confidence))
```

**DB 저장 값 요약:**

| decision | needs_l5 | confidence | coverage | ambiguity |
|----------|----------|-----------|---------|----------|
| `L3_ONLY` (정상) | `False` | `0.9` | `1.0` | `0.1` |
| `L3_ONLY` (degraded) | `False` | `0.35` | `0.6` | `0.65` |
| `NEEDS_L5` | `True` | `0.35` | `1.0` | `0.65` |
| `DEFERRED_HEAVY` | `True` | `0.35` | `0.0` | `0.65` |

---

### 3단계: L3PersistStage에서 L3+L4 동시 저장

**파일:** `src/sari/services/collection/l3/stages/persist_stage.py:100-153`

`apply_l3_only_success()` 호출 시 **L3와 L4 upsert가 항상 함께** 저장된다.

```python
# persist_stage.py:111-125
context.l3_layer_upsert = self._layer_upsert_builder.build_l3(...)  # symbols, degraded
context.l4_layer_upsert = self._layer_upsert_builder.build_l4(...)  # needs_l5, confidence, ...
```

→ Phase 1 완료 후 `tool_data_l4_normalized_symbols.needs_l5` 플래그가
  **반드시 DB에 기록**되므로 Phase 2의 `list_needs_l5_upgrade()` 쿼리가 정상 동작한다.

---

### Phase 2에서 needs_l5 활용 방식

```
Phase 1 완료 시 DB 상태:
  tool_data_l3_symbols     → symbols_json (tree-sitter 품질)
  tool_data_l4_normalized_symbols → needs_l5=1, confidence=0.35 (L5 필요 파일)

Phase 2 (L5AsyncUpgradeWatcher):
  SELECT ... WHERE needs_l5=1 AND s.workspace_id IS NULL (LEFT JOIN null 체크)
  → L5 미완료 파일만 선택 (중복 처리 방지)
  → confidence ASC 정렬 (신뢰도 낮은 파일 우선 처리)
```

**L5 upgrade 완료 후 L4 메트릭 갱신** (선택적):

L5 처리 성공 시 `apply_l5_success()` (persist_stage.py:155-208)가 호출되며
`build_l4()`를 다시 호출해 `admit_l5=True` 등의 admission 결과가 덮어쓰인다.
단, Phase 2에서는 `admission_decision=None`으로 전달해도 무방하다
(품질 메트릭 자체는 `preprocess_result`에서 계산되므로 변하지 않음).

---

## 증분 인덱싱 및 재기동 시나리오 분석

최초 인덱싱 외 세 가지 추가 시나리오를 커버해야 한다.

### 전제: enqueue_source로 요청 출처 구분

`file_enrich_queue.enqueue_source` 값:

| 값 | 출처 | LSP 상태 |
|----|------|---------|
| `"scan"` | L1 scheduler가 주기적으로 실행하는 full scan | cold 가능 |
| `"watcher"` | 파일 시스템 변경 감지 (event_watcher.py:283, 293) | **이미 warm** |
| `"manual"` | 외부 API로 수동 트리거 | 알 수 없음 |
| `"l5"` | L3 완료 후 L5 lane으로 handoff된 job | - |

→ **`"watcher"` 출처는 이전 인덱싱으로 LSP가 이미 warm한 상태**이므로 cold start 문제가 없다.
→ EventBus 설계에서는 모든 출처의 L3 flush가 동일한 `L3FlushCompleted` 이벤트로 통합되므로
→ `enqueue_source` 기반 분기 없이 watcher/scan/manual 모두 동일 경로로 처리된다.

---

### 시나리오 A: 재기동, 파일 변경 없음

```
이전 run: L3 완료, needs_l5=1 기록 → Phase 2가 중단(daemon kill 등)으로 미완료
재기동:   L1 scan → content_hash 동일 → enqueue skip
          Wave2 prewarm 약함 (변경 파일 없음) → Phase 2 trigger 없음
결과:     needs_l5=1 파일이 L5 미완료 상태로 영구 잔류  ← 문제
```

**해결**: daemon 시작 시 `L5AsyncUpgradeWatcher.trigger_startup()` 호출

```python
# L5AsyncUpgradeWatcher 메서드 (Step 3에서 상세 정의)
def trigger_startup(self, *, repo_root: str) -> None:
    """daemon 재기동 시 이전 run 미완료 L5 파일 검출.

    stale count > 0 이면 해당 repo_root를 즉시 활성화하고
    합성 L3FlushCompleted 이벤트를 발행하여 watcher loop를 깨운다.
    """
    if not self._enabled:
        return
    stale_count = self._tool_layer_repo.count_needs_l5_stale(
        workspace_id=self._workspace_id, repo_root=repo_root,
    )
    if stale_count > 0:
        log.info("startup: %d stale L5 files detected (repo=%s)", stale_count, repo_root)
        with self._lock:
            self._activated_repos.add(repo_root)  # LspWarmReady 없이 직접 활성화
        self._event_bus.publish(
            L3FlushCompleted(repo_root=repo_root, flushed_count=stale_count),
        )
```

`trigger_startup()` 호출 위치: `FileCollectionService.start_background()` (Step 4-C 참조).

---

### 시나리오 B: 재기동, 일부 파일 변경

```
재기동:   L1 scan → 변경 파일 detect → enqueue_source="scan" → L3 re-process
          → 새 content_hash로 tool_data_l4_normalized_symbols upsert
          → needs_l5=1 재기록
          → 이전 L5 semantics는 content_hash 불일치로 자연 무효화 (list_needs_l5_upgrade가 새 hash만 조회)
          → 참고: drop_stale_l5_semantics()는 현재 dead code — 필요 시 별도 배선 필요

Phase 2:  LspWarmReady 이벤트 → watcher 활성화 → L3FlushCompleted에 반응 → 정상 처리
          OR trigger_startup()이 먼저 실행 → 직접 활성화 + 합성 이벤트 → 정상 처리
```

→ **이 시나리오는 시나리오 A의 `trigger_startup()` 추가로 자동 해결됨.**

단, `list_needs_l5_upgrade()` 쿼리에서 **현재 content_hash** 기준으로 필터해야
이전 버전의 L5 레코드와 혼동되지 않는다 (후술).

---

### 시나리오 C: Watcher 단일 파일 변경 (증분)

```
사용자 파일 편집 → watcher detect → _index_file_with_priority(..., enqueue_source="watcher")
→ L3 re-process → needs_l5=1 → L3 flush
→ EventBus: L3FlushCompleted publish
→ L5AsyncUpgradeWatcher: 즉시 wake → DB 조회 → enqueue(enqueue_source="l5")
→ L5 처리
```

**해결**: EventBus 기반 통합 — watcher/scan 구분 불필요

watcher 파일도 L3 처리 후 `L3FlushCompleted` 이벤트가 발행되므로,
L5AsyncUpgradeWatcher가 즉시 반응하여 L5 enqueue를 수행한다.
`handoff_to_l5` 분기 로직이 완전히 제거되어 코드가 단순해진다.

> **지연 시간**: L3 flush → EventBus wake → DB 조회 → enqueue = 수십~수백 ms.
> 기존 즉시 handoff 대비 무시할 수 있는 수준.

---

### Content Hash 스탈레니스 처리

`tool_data_l4_normalized_symbols`와 `tool_data_l5_semantics` 모두
PK에 `content_hash`가 포함되어 있다.

파일이 변경되면:
- 새 `content_hash`로 L4 레코드가 upsert됨 (새 PK)
- 이전 `content_hash`의 L5 레코드: `drop_stale_l5_semantics()` 메서드가 구현되어 있으나
  **현재 호출하는 곳이 없음** (dead code, `tool_data_layer_repository.py:400-426`).
  → 이전 L5 레코드가 DB에 잔류하지만, 쿼리가 현재 `content_hash` 기준 JOIN을 사용하므로
    결과에 영향 없음. 다만 **DB 팽창 리스크**가 존재하므로, 구현 시 `drop_stale_l5_semantics()`를
    L5 처리 완료 후 또는 주기적 cleanup에서 호출하도록 배선 검토 필요.

`list_needs_l5_upgrade()` 쿼리는 반드시 **현재 활성 content_hash** + **workspace_id** 기준으로 JOIN:

> **중요**: `tool_data_l4_normalized_symbols`와 `tool_data_l5_semantics`의 PK에는 `workspace_id`가 포함된다.
> 기존 코드(`tool_data_layer_repository.py:283-287`)는 `_workspace_id_candidates_for_effective()`로
> 최대 5개 후보 workspace_id를 생성하여 `IN (...)` 조건으로 필터한다.
> 이 쿼리에서도 동일 패턴을 적용해야 cross-workspace 결과 오염을 방지할 수 있다.

```sql
SELECT
    f.repo_root,
    f.relative_path,
    f.content_hash,
    q.confidence
FROM collected_files_l1 f
JOIN tool_data_l4_normalized_symbols q
    ON  f.repo_root      = q.repo_root
    AND f.relative_path  = q.relative_path
    AND f.content_hash   = q.content_hash   -- ← 현재 버전만
LEFT JOIN tool_data_l5_semantics s
    ON  q.workspace_id   = s.workspace_id   -- ← workspace_id 일치 조건 필수
    AND f.repo_root      = s.repo_root
    AND f.relative_path  = s.relative_path
    AND f.content_hash   = s.content_hash   -- ← 현재 버전의 L5만 체크
WHERE f.repo_root = :repo_root
  AND f.is_deleted = 0
  AND q.workspace_id IN (:ws1, :ws2, :ws3, :ws4, :ws5)  -- workspace_id 후보 필터
  AND q.needs_l5 = 1
  AND s.workspace_id IS NULL               -- 현재 버전에 대한 L5 없음 (LEFT JOIN null 체크)
ORDER BY q.confidence ASC
LIMIT :limit
```

> `L5AsyncUpgradeWatcher`에 `workspace_id` (또는 해석 함수)를 주입해야 한다.

이 쿼리로 다음 세 케이스 모두 처리:
1. 신규 파일 (L5 없음) → `s.workspace_id IS NULL`
2. 변경 파일 (이전 L5 있으나 새 hash에 대한 L5 없음) → `content_hash` JOIN으로 구분
3. 이미 L5 완료된 파일 (current hash) → 제외됨

---

### 시나리오별 처리 경로 요약

| 시나리오 | enqueue_source | handoff_to_l5 | Phase 2 트리거 |
|---------|---------------|--------------|---------------|
| 최초 인덱싱 | `scan` | `None` | `LspWarmReady` + `L3FlushCompleted` |
| 재기동 (변경 없음) | (enqueue 없음) | - | `trigger_startup()` → 직접 활성화 + 합성 `L3FlushCompleted` |
| 재기동 (파일 변경) | `scan` | `None` | `LspWarmReady` + `L3FlushCompleted` |
| Watcher 단일 변경 | `watcher` | `None` | `L3FlushCompleted` (LSP 이미 warm) |

> **watcher 통합**: 모든 출처에서 `handoff_to_l5=None`. L5 처리는 EventBus를 통해
> L5AsyncUpgradeWatcher가 일괄 담당. watcher 파일도 L3 flush 후 즉시 처리됨.

---

## 구현 계획

### Step 0: EventBus 인프라 도입

**신규 파일:** `src/sari/core/event_bus.py`

범용 pub/sub EventBus. 이번 Phase 2 전용이 아닌, 코드베이스 전반에서 재사용 가능한 인프라.

```python
"""범용 EventBus — typed pub/sub with optional queue-based subscription."""

from __future__ import annotations

import logging
import queue
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

_SENTINEL = object()  # shutdown 시 queue에 주입하는 종료 마커


class EventBus:
    """Thread-safe pub/sub event bus.

    두 가지 구독 방식:
      1. subscribe(event_type, handler) — 콜백 기반, publisher 스레드에서 동기 실행
      2. subscribe_queue(event_types) — Queue 반환, subscriber가 자체 스레드에서 소비

    사용 예시:
        bus = EventBus()

        # 콜백 구독
        bus.subscribe(LspWarmReady, lambda e: print(e))

        # Queue 구독 (여러 이벤트 타입 가능)
        q = bus.subscribe_queue([L3FlushCompleted, LspWarmReady])
        event = q.get(timeout=5.0)  # blocking

        # 발행
        bus.publish(L3FlushCompleted(repo_root="/repo", flushed_count=10))

        # 종료
        bus.shutdown()  # 모든 queue에 sentinel 전달
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)
        self._queues: dict[type, list[queue.Queue[Any]]] = defaultdict(list)
        self._shutdown = False

    def subscribe(self, event_type: type[T], handler: Callable[[T], None]) -> None:
        """콜백 기반 구독. publisher 스레드에서 동기 호출된다."""
        with self._lock:
            self._handlers[event_type].append(handler)

    def subscribe_queue(
        self,
        event_types: list[type],
        *,
        maxsize: int = 0,
    ) -> queue.Queue[Any]:
        """Queue 기반 구독. 여러 이벤트 타입을 하나의 Queue로 수신.

        subscriber는 자체 스레드에서 queue.get(timeout=...)으로 소비.
        shutdown() 시 _SENTINEL이 주입되므로 `is_sentinel()`로 종료 감지.

        Args:
            event_types: 구독할 이벤트 타입 리스트
            maxsize: Queue 최대 크기 (0=무제한)

        Returns:
            이벤트가 들어오는 Queue 인스턴스
        """
        q: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        with self._lock:
            for et in event_types:
                self._queues[et].append(q)
        return q

    def publish(self, event: object) -> None:
        """이벤트 발행. 등록된 콜백 호출 + Queue에 put.

        콜백에서 발생하는 예외는 로깅 후 무시 (publisher를 블로킹하지 않음).
        Queue가 가득 찬 경우 put_nowait 실패를 로깅 후 무시.
        """
        if self._shutdown:
            return
        event_type = type(event)
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))
            queues = list(self._queues.get(event_type, []))

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                log.exception("EventBus handler error for %s", event_type.__name__)

        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                log.warning("EventBus queue full, dropping %s", event_type.__name__)

    def shutdown(self) -> None:
        """모든 Queue 구독자에게 종료 신호를 보낸다."""
        self._shutdown = True
        with self._lock:
            all_queues: set[queue.Queue[Any]] = set()
            for qs in self._queues.values():
                all_queues.update(qs)
        for q in all_queues:
            try:
                q.put_nowait(_SENTINEL)
            except queue.Full:
                pass

    @staticmethod
    def is_sentinel(event: object) -> bool:
        """Queue에서 꺼낸 이벤트가 종료 마커인지 확인."""
        return event is _SENTINEL
```

**이벤트 타입 정의:** `src/sari/core/events.py`

```python
"""EventBus 이벤트 타입 정의."""

from __future__ import annotations

from dataclasses import dataclass

from solidlsp.ls_config import Language


@dataclass(frozen=True)
class L3FlushCompleted:
    """L3 flush 후 L4 데이터(needs_l5 포함)가 DB에 적재되었을 때 발행."""
    repo_root: str
    flushed_count: int  # flush된 파일 수


@dataclass(frozen=True)
class LspWarmReady:
    """LSP warm-up 완료 후 발행 (Wave2 probe 스케줄 완료 시점)."""
    repo_root: str
    language: Language


@dataclass(frozen=True)
class ShutdownRequested:
    """daemon 종료 시 발행. 모든 subscriber에게 정리 기회 제공."""
    pass
```

**EventBus 인스턴스 생성 위치:** `src/sari/daemon_process.py`

```python
# daemon 시작 시 싱글턴 생성
from sari.core.event_bus import EventBus
event_bus = EventBus()

# FileCollectionService에 주입
service = FileCollectionService(
    ...
    event_bus=event_bus,
)

# daemon 종료 시
event_bus.shutdown()
```

---

### Step 1: L4 역할 재정의 — gate 제거, quality annotation 유지

**파일:** `src/sari/services/collection/l4/l4_admission_service.py`

현재 L4의 역할:

| 역할 | 현재 코드 위치 | 변경 |
|------|--------------|------|
| L5 실시간 gate (rate limit 판정) | `evaluate_batch()` 전체 | **Phase 2 전용 정책으로 이전** |
| confidence/needs_l5 annotation | `layer_upsert_builder.py` | **유지** |

**변경 전** (현재 `l4_admission_service.py:21-47`):
```python
def evaluate_batch(
    self,
    *,
    repo_root: str,
    language_key: str,
    total_rate: float,
    batch_rate: float,
    cooldown_active: bool = False,
    reason_code: L5ReasonCode = L5ReasonCode.GOLDENSET_COVERAGE,
    caller: str = "enrich_engine",
    workload_kind: str = "INDEX_BUILD",
) -> L4AdmissionDecisionDTO:
    return self._policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=reason_code,
            mode=L5RequestMode.BATCH,
            ...
            workload_kind=workload_kind,
        ),
        language_key=language_key,
    )
```

**변경 후**: `evaluate_batch()` 메서드를 Phase 1에서 호출하지 않음.
Phase 2 전용 `evaluate_upgrade()` 메서드 추가:
```python
def evaluate_upgrade(
    self,
    *,
    repo_root: str,
    language_key: str,
) -> L4AdmissionDecisionDTO:
    """Phase 2 (async quality upgrade) 전용 어드미션. rate limit 없이 항상 permit."""
    return L4AdmissionDecisionDTO(
        admit_l5=True,
        reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
        mode=L5RequestMode.BATCH,
        workspace_uid=normalize_workspace_uid(repo_root),
        budget_cost=1,
    )
```

---

### Step 2: Phase 1 hot path에서 L5 handoff 완전 비활성화

**파일:** `src/sari/services/collection/service.py`

**변경 위치 1** — `FileCollectionService.__init__` 파라미터 추가 (line ~102):
```python
def __init__(
    self,
    ...
    event_bus: EventBus | None = None,                   # 신규
    l5_async_quality_upgrade_enabled: bool = True,       # 신규
    ...
) -> None:
```

**변경 위치 2** — 인스턴스 변수 저장 (line ~126 근처):
```python
self._event_bus = event_bus
self._l5_async_quality_upgrade_enabled = bool(l5_async_quality_upgrade_enabled)
```

**변경 위치 3** — `EnrichEngine` 생성부 (line ~263-278):

```python
# Phase 1에서 L5 handoff 완전 비활성화 (EventBus 기반 Phase 2가 대체)
l5_async_quality_upgrade_enabled=self._l5_async_quality_upgrade_enabled,
```

**변경 위치 4** — `enrich_engine_wiring.py:223` (handoff_to_l5 주입부):

**파일:** `src/sari/services/collection/enrich_engine_wiring.py`

```python
# 변경 전
handoff_to_l5=lambda job, now_iso: engine._enrich_queue_repo.handoff_running_to_l5(
    job_id=job.job_id, now_iso=now_iso
),

# 변경 후
# async upgrade 활성화 시: 모든 출처에서 handoff 비활성화
# L5 처리는 EventBus → L5AsyncUpgradeWatcher가 일괄 담당
handoff_to_l5=(
    None
    if getattr(engine, "_l5_async_quality_upgrade_enabled", False)
    else lambda job, now_iso: engine._enrich_queue_repo.handoff_running_to_l5(
        job_id=job.job_id, now_iso=now_iso
    )
),
```

> **이전 설계와의 차이**: watcher/manual 출처도 `handoff_to_l5=None`.
> EventBus 기반 L5AsyncUpgradeWatcher가 L3 flush 이벤트에 즉시 반응하므로
> `enqueue_source` 분기가 불필요. 코드가 대폭 단순화된다.

---

### Step 3: L5AsyncUpgradeWatcher 신설 (EventBus subscriber)

**신규 파일:** `src/sari/services/collection/l5/upgrade_watcher.py`

```python
"""EventBus 기반 L5 비동기 품질 교정 watcher.

L3 flush 이벤트에 반응하여 needs_l5=1 파일을 즉시 L5 queue에 enqueue한다.
LSP warm 완료 전까지는 이벤트를 수신하되 처리하지 않고 대기.
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import UTC, datetime

from sari.core.event_bus import EventBus
from sari.core.events import L3FlushCompleted, LspWarmReady, ShutdownRequested

log = logging.getLogger(__name__)


class L5AsyncUpgradeWatcher:
    """Phase 2: EventBus subscriber로 L3 flush에 즉시 반응하여 L5 enqueue.

    라이프사이클:
      1. start() → EventBus 구독 + watcher 스레드 시작
      2. LspWarmReady 수신 → _activated=True (해당 repo_root에 대해)
      3. L3FlushCompleted 수신 → activated 상태이면 즉시 DB 조회 → batch enqueue
      4. timeout(poll_interval) → 주기적 DB 조회 (누락 방지 fallback)
      5. ShutdownRequested 수신 OR EventBus.shutdown() → loop 종료

    이전 L5AsyncQualityUpgradeJob과의 차이:
      - warm_delay_sec 제거 → 이벤트 기반 즉시 반응
      - 단일 trigger 후 종료 → 지속 감시 루프
      - watcher/scan 분기 불필요 → 모든 출처의 L3 flush를 동일하게 처리
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        enrich_queue_repo: object,
        tool_layer_repo: object,
        workspace_id: str,
        batch_size: int = 50,
        poll_interval_sec: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self._event_bus = event_bus
        self._enrich_queue_repo = enrich_queue_repo
        self._tool_layer_repo = tool_layer_repo
        self._workspace_id = str(workspace_id)
        self._batch_size = max(1, int(batch_size))
        self._poll_interval_sec = max(0.5, float(poll_interval_sec))
        self._enabled = bool(enabled)

        # 활성화된 repo_root 집합 (LspWarmReady 수신 시 추가)
        self._activated_repos: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._event_queue: queue.Queue[object] | None = None

    def start(self) -> None:
        """EventBus 구독 및 watcher 스레드 시작."""
        if not self._enabled:
            return
        self._event_queue = self._event_bus.subscribe_queue(
            [L3FlushCompleted, LspWarmReady, ShutdownRequested],
        )
        self._thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="l5-upgrade-watcher",
        )
        self._thread.start()
        log.info("L5AsyncUpgradeWatcher started")

    def trigger_startup(self, *, repo_root: str) -> None:
        """daemon 재기동 시 미완료 L5 파일 감지.

        stale count > 0 이면 해당 repo_root를 즉시 활성화하고
        합성 이벤트를 발행하여 watcher loop를 깨운다.

        주의: tool_data_l4/l5 테이블에 language 컬럼이 없으므로
        repo_root 단위로만 stale count 조회.
        """
        if not self._enabled:
            return
        stale_count = self._tool_layer_repo.count_needs_l5_stale(
            workspace_id=self._workspace_id, repo_root=repo_root,
        )
        if stale_count > 0:
            log.info(
                "startup: %d stale L5 files detected (repo=%s)",
                stale_count, repo_root,
            )
            with self._lock:
                self._activated_repos.add(repo_root)
            # 합성 이벤트로 watcher loop 즉시 깨우기
            self._event_bus.publish(
                L3FlushCompleted(repo_root=repo_root, flushed_count=stale_count),
            )

    def _watch_loop(self) -> None:
        """메인 감시 루프. EventBus Queue에서 이벤트를 소비한다."""
        assert self._event_queue is not None
        while True:
            # 이벤트 대기 (timeout으로 주기적 fallback 보장)
            try:
                event = self._event_queue.get(timeout=self._poll_interval_sec)
            except queue.Empty:
                # timeout: activated repo에 대해 주기적 조회
                self._process_all_activated_repos()
                continue

            # 종료 감지
            if EventBus.is_sentinel(event):
                log.info("L5AsyncUpgradeWatcher received shutdown signal")
                break

            # 이벤트 처리
            if isinstance(event, ShutdownRequested):
                log.info("L5AsyncUpgradeWatcher shutting down")
                break

            if isinstance(event, LspWarmReady):
                with self._lock:
                    self._activated_repos.add(event.repo_root)
                log.info(
                    "L5 upgrade activated for repo=%s (language=%s)",
                    event.repo_root, event.language.value,
                )
                # 활성화 직후 즉시 처리 시도
                self._process_batch(repo_root=event.repo_root)
                continue

            if isinstance(event, L3FlushCompleted):
                with self._lock:
                    activated = event.repo_root in self._activated_repos
                if activated:
                    # drain: 짧은 시간 내 여러 flush 이벤트를 모아 한 번에 처리
                    self._drain_and_process(event.repo_root)
                # else: LSP 아직 미준비 → 무시 (warm 후 자동 처리됨)
                continue

    def _drain_and_process(self, repo_root: str) -> None:
        """Queue에 쌓인 동일 repo_root flush 이벤트를 drain 후 batch 처리."""
        assert self._event_queue is not None
        # 짧은 시간 내 추가 이벤트를 drain (batch 집계)
        drained = 0
        while True:
            try:
                extra = self._event_queue.get_nowait()
                if EventBus.is_sentinel(extra) or isinstance(extra, ShutdownRequested):
                    # shutdown 감지 시 이벤트를 다시 넣고 루프 종료 위임
                    self._event_queue.put(extra)
                    break
                if isinstance(extra, LspWarmReady):
                    with self._lock:
                        self._activated_repos.add(extra.repo_root)
                drained += 1
            except queue.Empty:
                break
        self._process_batch(repo_root=repo_root)

    def _process_all_activated_repos(self) -> None:
        """timeout 시 모든 활성 repo에 대해 처리 (fallback)."""
        with self._lock:
            repos = list(self._activated_repos)
        for repo_root in repos:
            self._process_batch(repo_root=repo_root)

    def _process_batch(self, *, repo_root: str) -> None:
        """DB에서 needs_l5 파일을 조회하여 L5 queue에 enqueue."""
        now_iso = datetime.now(UTC).isoformat()
        try:
            files = self._tool_layer_repo.list_needs_l5_upgrade(
                workspace_id=self._workspace_id,
                repo_root=repo_root,
                limit=self._batch_size,
            )
        except Exception:
            log.exception("L5 upgrade query failed (repo=%s)", repo_root)
            return

        if not files:
            return

        enqueued = 0
        for file_dto in files:
            try:
                self._enrich_queue_repo.enqueue(
                    repo_root=file_dto.repo_root,
                    relative_path=file_dto.relative_path,
                    content_hash=file_dto.content_hash,
                    priority=20,
                    enqueue_source="l5",
                    now_iso=now_iso,
                )
                enqueued += 1
            except Exception:
                log.debug(
                    "L5 upgrade enqueue failed (path=%s)",
                    file_dto.relative_path,
                )
        log.info(
            "L5 upgrade: enqueued %d / %d files (repo=%s)",
            enqueued, len(files), repo_root,
        )
```

**이전 `L5AsyncQualityUpgradeJob`과의 핵심 차이:**

| 항목 | 기존 (Job) | 신규 (Watcher) |
|------|-----------|---------------|
| 트리거 | `trigger()` → delay → 단일 batch | EventBus 이벤트 → 즉시 반응 |
| warm 대기 | `time.sleep(warm_delay_sec)` | `LspWarmReady` 이벤트까지 비활성 |
| 지속 감시 | 없음 (1회 실행 후 종료) | 상시 루프 (poll_interval fallback) |
| watcher 처리 | 별도 `handoff_to_l5` 분기 | 동일 경로 (L3FlushCompleted) |
| 스레드 | trigger마다 새 스레드 | 단일 daemon 스레드 |
| 종료 | 없음 | `ShutdownRequested` / sentinel |

---

### Step 4: EventBus publish 연동 (LSP warm + L3 flush)

#### 4-A: LSP warm 이벤트 발행

**파일:** `src/sari/services/collection/repo_support.py`

`configure_lsp_prewarm_languages()` 메서드에 `event_bus` 주입. 콜백 대신 EventBus publish:

**변경 전** (현재 시그니처):
```python
def configure_lsp_prewarm_languages(
    self,
    repo_root: str,
    language_counts: dict[Language, int],
    language_sample_files: dict[Language, str] | None = None,
) -> None:
```

**변경 후**:
```python
def configure_lsp_prewarm_languages(
    self,
    repo_root: str,
    language_counts: dict[Language, int],
    language_sample_files: dict[Language, str] | None = None,
) -> None:
    ...
    # 기존 Wave2 probe 스케줄 코드 (line 72-87) 유지

    # 신규: hot 언어 확정 후 EventBus로 LspWarmReady 발행
    if self._event_bus is not None:
        for language in selected:
            try:
                self._event_bus.publish(
                    LspWarmReady(repo_root=repo_root, language=language),
                )
            except Exception:
                pass
```

**`RepoSupport.__init__`에 `event_bus` 파라미터 추가:**
```python
def __init__(
    self,
    ...
    event_bus: EventBus | None = None,   # 신규
) -> None:
    ...
    self._event_bus = event_bus
```

> **변경 없음**: `service.py`의 `FileScanner` 생성부는 기존 `configure_lsp_prewarm_languages` 콜백을
> 그대로 사용. `on_lsp_warm` 콜백 파라미터가 제거되어 lambda wrapping 불필요.

#### 4-B: L3 flush 이벤트 발행

**파일:** `src/sari/services/collection/l3/l3_flush_coordinator.py` (또는 flush가 실행되는 지점)

L3 flush 완료 후 `L3FlushCompleted` 이벤트를 발행:

```python
# L3 flush 완료 후 (done_ids persist 직후)
if self._event_bus is not None and flushed_count > 0:
    self._event_bus.publish(
        L3FlushCompleted(repo_root=repo_root, flushed_count=flushed_count),
    )
```

> **주입 경로**: `event_bus`는 `FileCollectionService` → `EnrichEngine` → flush coordinator로 전달.
> 기존 DI 패턴(콜백 주입)과 동일한 경로로 주입하되, 단일 `EventBus` 인스턴스만 전달.
>
> **구현 시 주의**: 실제 L3 flush(DB persist)는 `enrich_flush_coordinator.py`에서 발생하며,
> `l3_flush_coordinator.py`는 L3 그룹 단위 집계를 담당한다. `repo_root`는 flush 메서드의
> 직접 파라미터가 아니라 개별 upsert payload 내에 포함되어 있으므로, `L3FlushCompleted`
> 이벤트 발행 시 `repo_root`를 상위 호출자(`l3_group_processor.py`)에서 전달받거나
> flush 대상 payload에서 추출하는 방안이 필요하다.

#### 4-C: L5AsyncUpgradeWatcher 생성 및 시작

**`service.py`의 `FileCollectionService.__init__`에서:**
```python
# L5AsyncUpgradeWatcher 생성
self._l5_upgrade_watcher = L5AsyncUpgradeWatcher(
    event_bus=self._event_bus,
    enrich_queue_repo=self._enrich_queue_repo,
    tool_layer_repo=tool_layer_repo,
    workspace_id=self._workspace_id,
    batch_size=l5_async_quality_upgrade_batch_size,
    poll_interval_sec=l5_async_quality_upgrade_poll_interval_sec,
    enabled=self._l5_async_quality_upgrade_enabled,
)
```

**`FileCollectionService.start_background()` 또는 초기화 시:**
```python
# watcher 스레드 시작
self._l5_upgrade_watcher.start()

# 재기동 시 stale 파일 감지
self._l5_upgrade_watcher.trigger_startup(repo_root=self._repo_root)
```

---

### Step 5: DB query 지원 — needs_l5 파일 조회

**파일:** `src/sari/db/repositories/tool_data_layer_repository.py` (또는 신규 repo)

> **주의**: `tool_data_l4_normalized_symbols`와 `tool_data_l5_semantics`에는 language 컬럼이 없다
> (`schema.py:255`, `schema.py:276`). 따라서 언어별 필터는 SQL 레벨에서 불가 — `repo_root` 단위로만 조회.

신규 메서드 추가:
```python
def list_needs_l5_upgrade(
    self,
    *,
    workspace_id: str,
    repo_root: str,
    limit: int = 50,
) -> list[FileNeedsUpgradeDTO]:
    """needs_l5=1이고 L5 결과가 없는 파일 목록을 반환한다.

    workspace_id는 _workspace_id_candidates()로 후보 리스트를 생성하여 IN 조건에 사용.

    SQL:
        -- content_hash를 3-way JOIN하여 현재 활성 버전만 대상으로 한다.
        -- workspace_id IN (...) 패턴은 기존 코드 준용 (tool_data_layer_repository.py:283-287)
        SELECT
            f.repo_root,
            f.relative_path,
            f.content_hash,
            q.confidence
        FROM collected_files_l1 f
        JOIN tool_data_l4_normalized_symbols q
            ON  f.repo_root     = q.repo_root
            AND f.relative_path = q.relative_path
            AND f.content_hash  = q.content_hash   -- 현재 버전만
        LEFT JOIN tool_data_l5_semantics s
            ON  q.workspace_id  = s.workspace_id   -- workspace 일치
            AND f.repo_root     = s.repo_root
            AND f.relative_path = s.relative_path
            AND f.content_hash  = s.content_hash   -- 현재 버전 L5만 체크
        WHERE f.repo_root = :repo_root
          AND f.is_deleted = 0
          AND q.workspace_id IN (:ws1, :ws2)       -- workspace_id 후보
          AND q.needs_l5 = 1
          AND s.workspace_id IS NULL               -- 현재 버전 L5 없음
        ORDER BY q.confidence ASC                  -- 낮은 confidence 우선
        LIMIT :limit

def count_needs_l5_stale(
    self,
    *,
    workspace_id: str,
    repo_root: str,
) -> int:
    -- trigger_startup 용:
        SELECT COUNT(*)
        FROM collected_files_l1 f
        JOIN tool_data_l4_normalized_symbols q
            ON f.repo_root = q.repo_root
            AND f.relative_path = q.relative_path
            AND f.content_hash = q.content_hash
        LEFT JOIN tool_data_l5_semantics s
            ON q.workspace_id = s.workspace_id
            AND f.repo_root = s.repo_root
            AND f.relative_path = s.relative_path
            AND f.content_hash = s.content_hash
        WHERE f.repo_root = :repo_root
          AND f.is_deleted = 0
          AND q.workspace_id IN (:ws1, :ws2)
          AND q.needs_l5 = 1
          AND s.workspace_id IS NULL
    """
    ...
```

---

### Step 6: enrich_queue에 L5 직행 enqueue

**파일:** `src/sari/db/repositories/file_enrich_queue_repository.py`

> **신규 메서드 불필요**: 기존 `enqueue()` 메서드가 `enqueue_source` 파라미터를 받고 `(repo_root, relative_path)` 중복 PENDING/FAILED job에 대해 upsert 처리를 내장하고 있다 (`file_enrich_queue_repository.py:21`, `:54-98`).
> `enqueue_for_l5_direct()`를 별도로 만들면 기존 계약(dedupe, repo_id 생성 등)과 중복이 생긴다.

Phase 2에서는 기존 `enqueue()` 직접 호출:
```python
# L5AsyncUpgradeWatcher._process_batch() 내부
now_iso = datetime.now(UTC).isoformat()
self._enrich_queue_repo.enqueue(
    repo_root=file_dto.repo_root,
    relative_path=file_dto.relative_path,
    content_hash=file_dto.content_hash,
    priority=20,
    enqueue_source="l5",   # acquire_pending_for_l5()가 이 값으로 조회
    now_iso=now_iso,
)
```
- `enqueue_source='l5'`: `acquire_pending_for_l5()`가 이 source를 기준으로 조회 (`file_enrich_queue_repository.py:369`)
- 중복 enqueue 시 기존 PENDING/FAILED job을 upsert → 중복 job 생성 없음

---

### Step 7: Config 추가

**파일:** `src/sari/core/config_model.py` (line ~295 이후, `# L5 admission/token budget` 섹션 근처)

```python
# L5 Async Quality Upgrade (Phase 2, EventBus 기반)
l5_async_quality_upgrade_enabled: bool = True
l5_async_quality_upgrade_batch_size: int = 50
l5_async_quality_upgrade_poll_interval_sec: float = 5.0
```

> `warm_delay_sec` 삭제: 이벤트 기반으로 전환되어 지연 대기 불필요.
> `poll_interval_sec` 신규: EventBus 이벤트 누락 시 fallback 주기적 조회 간격.

**파일:** `src/sari/core/config_fields.py` (`_build_extended_fields()` 내):
```python
_ConfigField(
    "l5_async_quality_upgrade_enabled_raw",
    "SARI_L5_ASYNC_QUALITY_UPGRADE_ENABLED",
    "l5_async_quality_upgrade_enabled",
    True,
    lower=True,
),
_ConfigField(
    "l5_async_quality_upgrade_batch_size_raw",
    "SARI_L5_ASYNC_QUALITY_UPGRADE_BATCH_SIZE",
    "l5_async_quality_upgrade_batch_size",
    50,
),
_ConfigField(
    "l5_async_quality_upgrade_poll_interval_sec_raw",
    "SARI_L5_ASYNC_QUALITY_UPGRADE_POLL_INTERVAL_SEC",
    "l5_async_quality_upgrade_poll_interval_sec",
    5.0,
),
```

**파일:** `src/sari/core/config_default_loader.py` (cls() 호출부에 추가):
```python
l5_async_quality_upgrade_enabled=parser.bool_true(
    extended_raw_values["l5_async_quality_upgrade_enabled_raw"]
),
l5_async_quality_upgrade_batch_size=parser.non_negative_int(
    extended_raw_values["l5_async_quality_upgrade_batch_size_raw"], default=50
),
l5_async_quality_upgrade_poll_interval_sec=parser.non_negative_float(
    extended_raw_values["l5_async_quality_upgrade_poll_interval_sec_raw"], default=5.0
),
```

---

### Step 8: daemon_process.py / composition 지점에서 config 전달

**파일:** `src/sari/daemon_process.py` (또는 `FileCollectionService` 생성 지점)

```python
from sari.core.event_bus import EventBus

# EventBus 싱글턴 생성
event_bus = EventBus()

# FileCollectionService 생성 시 신규 파라미터 전달
service = FileCollectionService(
    ...
    event_bus=event_bus,
    l5_async_quality_upgrade_enabled=cfg.l5_async_quality_upgrade_enabled,
    l5_async_quality_upgrade_batch_size=cfg.l5_async_quality_upgrade_batch_size,
    l5_async_quality_upgrade_poll_interval_sec=cfg.l5_async_quality_upgrade_poll_interval_sec,
)

# daemon 종료 시
event_bus.shutdown()
```

---

### Step 9: 테스트

#### 9-A: EventBus 단위 테스트

**신규 테스트 파일:** `tests/unit/core/test_event_bus.py`

```python
def test_subscribe_callback_receives_published_event() -> None:
    """subscribe 콜백이 publish된 이벤트를 수신한다."""

def test_subscribe_queue_receives_published_event() -> None:
    """subscribe_queue로 반환된 Queue에 이벤트가 들어온다."""

def test_subscribe_queue_multiple_event_types() -> None:
    """여러 이벤트 타입을 하나의 Queue로 수신할 수 있다."""

def test_publish_does_not_propagate_handler_exception() -> None:
    """handler 예외가 publish 호출자에게 전파되지 않는다."""

def test_shutdown_sends_sentinel_to_all_queues() -> None:
    """shutdown() 시 모든 Queue에 sentinel이 전달된다."""

def test_publish_after_shutdown_is_noop() -> None:
    """shutdown 후 publish는 무시된다."""
```

#### 9-B: L5AsyncUpgradeWatcher 테스트

**신규 테스트 파일:** `tests/unit/l5/test_upgrade_watcher.py`

```python
def test_watcher_activates_on_lsp_warm_ready() -> None:
    """LspWarmReady 수신 시 해당 repo_root가 활성화된다."""

def test_watcher_processes_batch_on_l3_flush_after_activation() -> None:
    """활성화 상태에서 L3FlushCompleted 수신 시 batch enqueue가 실행된다."""

def test_watcher_ignores_l3_flush_before_activation() -> None:
    """LspWarmReady 미수신 상태에서 L3FlushCompleted는 무시된다."""

def test_watcher_processes_on_poll_timeout() -> None:
    """이벤트 없이 poll_interval 초과 시 주기적 조회가 실행된다."""

def test_watcher_stops_on_shutdown() -> None:
    """ShutdownRequested 수신 시 watch loop가 종료된다."""

def test_trigger_startup_activates_and_wakes() -> None:
    """trigger_startup()이 stale 파일 감지 시 활성화 + 합성 이벤트를 발행한다."""

def test_process_batch_enqueues_needs_l5_files() -> None:
    """needs_l5=1이고 L5 미완료인 파일만 enqueue된다."""

def test_watcher_handles_scan_and_watcher_files_uniformly() -> None:
    """scan/watcher 출처 파일이 동일한 경로로 L5 처리된다."""
```

#### 9-C: 기존 테스트 수정

**`tests/unit/misc/test_batch17_performance_hardening.py`**

L5 handoff 비활성화 관련 기존 테스트에 `l5_async_quality_upgrade_enabled=True` 명시 추가.

---

## 변경 파일 요약

| 파일 | 변경 내용 | 라인 참조 |
|------|----------|----------|
| `src/sari/core/event_bus.py` | **신규** — 범용 EventBus (`subscribe`, `subscribe_queue`, `publish`, `shutdown`) | - |
| `src/sari/core/events.py` | **신규** — 이벤트 타입 정의 (`L3FlushCompleted`, `LspWarmReady`, `ShutdownRequested`) | - |
| `src/sari/services/collection/l4/l4_admission_service.py` | `evaluate_upgrade()` 추가, Phase 1 gate 역할 제거 | 전체 (~48L) |
| `src/sari/services/collection/service.py` | `event_bus` + `l5_async_quality_upgrade_*` 파라미터 추가, `L5AsyncUpgradeWatcher` 생성/시작 | ~102, ~126 |
| `src/sari/services/collection/enrich_engine_wiring.py` | `handoff_to_l5=None` (async upgrade 시 완전 비활성화) | ~223 |
| `src/sari/services/collection/l5/upgrade_watcher.py` | **신규** — `L5AsyncUpgradeWatcher` (EventBus subscriber, 지속 감시 루프) | - |
| `src/sari/services/collection/repo_support.py` | `event_bus` 주입, `LspWarmReady` publish (`on_lsp_warm` 콜백 제거) | ~52, ~72 |
| `src/sari/services/collection/l3/l3_flush_coordinator.py` | `event_bus` 주입, flush 완료 후 `L3FlushCompleted` publish | flush 후 |
| `src/sari/db/repositories/tool_data_layer_repository.py` | `list_needs_l5_upgrade(workspace_id, repo_root, limit)`, `count_needs_l5_stale(workspace_id, repo_root)` 추가 | 신규 메서드 |
| `src/sari/db/repositories/file_enrich_queue_repository.py` | 변경 없음 — 기존 `enqueue(..., enqueue_source='l5')` 활용 | - |
| `src/sari/core/config_model.py` | `l5_async_quality_upgrade_*` 3개 필드 (`warm_delay_sec` 대신 `poll_interval_sec`) | ~295 이후 |
| `src/sari/core/config_fields.py` | 환경변수 필드 정의 3개 추가 | `_build_extended_fields()` 내 |
| `src/sari/core/config_default_loader.py` | 파싱 코드 3개 추가 | `cls()` 호출부 |
| `src/sari/daemon_process.py` | `EventBus` 생성 + config 전달 + `shutdown()` 호출 | `FileCollectionService` 생성 지점 |
| `tests/unit/core/test_event_bus.py` | **신규** — EventBus 단위 테스트 | - |
| `tests/unit/l5/test_upgrade_watcher.py` | **신규** — L5AsyncUpgradeWatcher 테스트 | - |

---

## 구현 우선순위

| 순위 | 단계 | 이유 | 예상 효과 |
|------|------|------|----------|
| 1 | Step 0: EventBus 인프라 | 모든 이벤트 기반 기능의 전제 | 범용 인프라 확보 |
| 2 | Step 2: hot path L5 handoff 비활성화 | 리스크 최소, 즉시 TPS 개선 | +20~40 TPS |
| 3 | Step 5+6: DB query/enqueue 지원 | Step 3 동작의 전제 | - |
| 4 | Step 3: `L5AsyncUpgradeWatcher` 신설 | 품질 회복의 핵심 | L5 처리율 0.8% → ~100% |
| 5 | Step 4: EventBus publish 연동 | L3 flush/LSP warm 이벤트 발행 | 즉시 반응 처리 |
| 6 | Step 7+8: Config 추가 | 운영 제어 | - |
| 7 | Step 1: L4 gate 제거 | 코드 정리 | - |

---

## 기대 효과

| 항목 | 현재 | 개선 후 |
|------|------|---------|
| 초기 인덱싱 TPS | ~157 TPS | ~200+ TPS (L5 handoff overhead 완전 제거) |
| bulk L5 처리율 | 0.8% | ~100% (EventBus 기반 즉시 반응) |
| L5 처리 시작 시점 | warm_delay 후 단일 batch | LSP warm + L3 flush 즉시 |
| watcher 파일 L5 | 별도 handoff 분기 | 동일 EventBus 경로 (코드 단순화) |
| 사용자 요청 지연 | Phase 2와 LSP pool 경쟁 | `request_kind` 분리 가능 |
| 확장성 | 없음 | EventBus로 향후 기능 추가 용이 |

---

## 트레이드오프

### 장점
- 초기 인덱싱 속도 최대화 (L5 overhead 완전 제거)
- 최종 품질은 LSP warm 후 100% 보장 (현재 0.8% → ~100%)
- Phase 1 완료 직후 바로 사용 가능한 L3 품질 제공
- **이벤트 기반 즉시 반응**: L3 flush 직후 L5 처리 시작 (delay 없음)
- **watcher 통합**: scan/watcher 구분 없이 단일 경로 → 코드 단순화
- **범용 EventBus**: 향후 다른 이벤트 기반 기능에 재사용 가능

### 단점 / 주의사항
- Phase 2 완료 전까지 L3 품질 기간 존재 (LSP warm + L3 flush → L5 처리 시간)
- **EventBus 스레드 안전성**: publish()가 publisher 스레드에서 콜백을 실행하므로
  handler 내부에서 blocking 작업을 하면 publisher가 지연됨.
  → L5AsyncUpgradeWatcher는 `subscribe_queue()`를 사용하므로 이 문제 없음.
- **Phase 2 recent success skip 리스크** (`decision_stage.py:52-55`):
  Phase 2에서 `enqueue_source='l5'`로 재enqueue된 job도 `decision_stage.evaluate()` 진입 시
  `is_recent_tool_ready(job)` 검사가 **l5_lane 여부와 무관하게 먼저 실행**된다.
  이전 L3 처리가 "최근 성공"으로 판단되면 job이 skip되어 L5 처리가 건너뛰어질 수 있음.
  → 구현 시 `skip_eligibility.is_recent_tool_ready()` 로직에서 `enqueue_source='l5'`인 경우를
    skip 면제하는지 확인 필요. 필요하면 `l5_lane=True` 경로에서 recent success skip을 우회하도록
    변경 검토.
- `list_needs_l5_upgrade()` 쿼리 성능 (대용량 repo에서 JOIN 부하)
  - 보완: `(repo_root, needs_l5, relative_path)` 인덱스 추가
- language 컬럼 부재로 언어별 SQL 필터 불가 → `_process_batch()`는 repo_root 전체를 대상으로 처리
- **watcher 파일 L5 지연 미세 증가**: 기존 즉시 handoff 대비 수십~수백 ms 추가 지연
  (L3 flush → EventBus → DB 조회 → enqueue). 실사용에 영향 없는 수준.
- **`drop_stale_l5_semantics()` 미배선**: 파일 변경 시 이전 content_hash의 L5 레코드를 삭제하는
  메서드가 구현되어 있으나 현재 호출하는 곳이 없음 (dead code). content_hash JOIN으로 정합성은
  유지되지만 DB 팽창 리스크 존재. 구현 시 cleanup 배선 검토 필요.
- **`_drain_and_process` 이벤트 소비 범위**: drain 시 Queue에서 꺼낸 `L3FlushCompleted`가
  현재 처리 중인 `repo_root`와 다른 repo의 이벤트일 수 있다. 이 경우 해당 이벤트는
  소비되었으나 즉시 처리되지 않는다. `poll_interval_sec` fallback이 이를 커버하므로
  실질적 데이터 손실은 없으나, 다중 repo 동시 처리 시 최대 `poll_interval_sec`만큼
  지연이 발생할 수 있다.

### 롤백 전략
```bash
# 문제 발생 시 즉시 비활성화
SARI_L5_ASYNC_QUALITY_UPGRADE_ENABLED=false sari daemon start
```
→ L5AsyncUpgradeWatcher가 비활성화되고 기존 동기 handoff 경로로 자동 복귀.
→ EventBus 자체는 유지되어 다른 subscriber에 영향 없음.

---

## 구현 완료 체크리스트

> 각 항목을 구현 후 직접 확인하여 체크한다.
> **PASS** = 확인 완료 / **FAIL** = 문제 발견 (원인 메모 필요)

---

### [ ] Step 0: EventBus 인프라

**코드 변경 확인**

- [ ] `src/sari/core/event_bus.py` 파일이 존재하는가
- [ ] `EventBus` 클래스가 아래 메서드를 모두 갖는가
  - [ ] `subscribe(event_type, handler)` — 콜백 기반 구독
  - [ ] `subscribe_queue(event_types, maxsize)` — Queue 기반 구독
  - [ ] `publish(event)` — 이벤트 발행 (handler 예외 무시, queue full 무시)
  - [ ] `shutdown()` — 모든 Queue에 sentinel 전달
  - [ ] `is_sentinel(event)` — sentinel 체크 정적 메서드
- [ ] `src/sari/core/events.py` 파일이 존재하는가
  - [ ] `L3FlushCompleted(repo_root, flushed_count)` — frozen dataclass
  - [ ] `LspWarmReady(repo_root, language)` — frozen dataclass
  - [ ] `ShutdownRequested` — frozen dataclass
- [ ] handler 예외가 publisher에게 전파되지 않는가 (로깅만)
- [ ] shutdown 후 publish가 무시되는가

---

### [ ] Step 1: L4 gate 제거

**코드 변경 확인**

- [ ] `l4_admission_service.py` — `evaluate_upgrade()` 메서드가 신설되고 `admit_l5=True`를 항상 반환하는가
- [ ] `evaluate_batch()`는 Phase 1 경로(`L3Orchestrator` → `L3AdmissionStage`)에서 더 이상 호출되지 않는가
- [ ] `layer_upsert_builder.py`의 `build_l4()` 로직(confidence/needs_l5/coverage 계산)은 변경 없이 유지되는가

---

### [ ] Step 2: Phase 1 hot path L5 handoff 완전 비활성화

**코드 변경 확인**

- [ ] `service.py` — `FileCollectionService.__init__` 에 `event_bus` + `l5_async_quality_upgrade_enabled` 파라미터가 추가되었는가
- [ ] `enrich_engine_wiring.py:~223` — async upgrade 활성화 시 `handoff_to_l5=None` 인가 (모든 출처)
- [ ] `l5_async_quality_upgrade_enabled=False` 일 때는 기존처럼 `handoff_to_l5` 람다가 설정되는가 (하위 호환)

**동작 검증**

- [ ] L3 완료 후 `file_enrich_queue`에 `handoff_running_to_l5` 경로로 생성된 `enqueue_source='l5'` 레코드가 없는가
  ```sql
  SELECT COUNT(*) FROM file_enrich_queue WHERE enqueue_source = 'l5';
  -- Phase 2 watcher 동작 전: 0이어야 함
  ```

---

### [ ] Step 3: L5AsyncUpgradeWatcher 신설

**코드 변경 확인**

- [ ] `src/sari/services/collection/l5/upgrade_watcher.py` 파일이 존재하는가
- [ ] `L5AsyncUpgradeWatcher` 클래스가 아래 메서드를 모두 갖는가
  - [ ] `start()` — EventBus 구독 + watcher 스레드 시작
  - [ ] `trigger_startup(repo_root)` — stale 파일 감지 → 활성화 + 합성 이벤트
  - [ ] `_watch_loop()` — 메인 감시 루프
  - [ ] `_process_batch(repo_root)` — DB 조회 → batch enqueue
- [ ] `LspWarmReady` 수신 전에는 `L3FlushCompleted`를 무시하는가
- [ ] `ShutdownRequested` 또는 sentinel 수신 시 루프가 종료되는가
- [ ] poll_interval timeout 시 활성 repo에 대해 주기적 조회가 실행되는가

---

### [ ] Step 4: EventBus publish 연동

**코드 변경 확인**

- [ ] `repo_support.py` — `__init__`에 `event_bus` 파라미터가 추가되었는가
- [ ] `repo_support.py` — Wave2 probe 스케줄 후 `LspWarmReady` 이벤트가 publish되는가
- [ ] L3 flush 완료 후 `L3FlushCompleted` 이벤트가 publish되는가 (flush coordinator 또는 해당 지점)
- [ ] `daemon_process.py` — daemon 종료 시 `event_bus.shutdown()` 이 호출되는가

**재기동 fallback 확인**

- [ ] `start_background()` 에서 `trigger_startup(repo_root)` 이 호출되는가
- [ ] stale count > 0 시 합성 `L3FlushCompleted` 이벤트가 발행되어 watcher가 깨어나는가

---

### [ ] Step 5: DB query 지원

**코드 변경 확인**

- [ ] `tool_data_layer_repository.py` — `list_needs_l5_upgrade()` 메서드가 추가되었는가
- [ ] `tool_data_layer_repository.py` — `count_needs_l5_stale()` 메서드가 추가되었는가

**쿼리 정합성 확인**

- [ ] `list_needs_l5_upgrade()` 쿼리가 `collected_files_l1`, `tool_data_l4_normalized_symbols`, `tool_data_l5_semantics` 를 **content_hash 포함 3-way JOIN** 하는가
  ```sql
  AND f.content_hash = q.content_hash      -- L4: 현재 버전만
  AND q.workspace_id = s.workspace_id      -- L5: 동일 workspace
  AND f.content_hash = s.content_hash      -- L5: 현재 버전 체크
  AND q.workspace_id IN (:ws1, :ws2)       -- workspace_id 후보 필터
  AND s.workspace_id IS NULL               -- L5 미완료 (LEFT JOIN null 체크)
  AND f.is_deleted = 0                     -- 삭제 파일 제외
  ```
- [ ] `ORDER BY q.confidence ASC` — 낮은 confidence 파일이 먼저 처리되는가

---

### [ ] Step 6: enrich_queue L5 직행 enqueue

**코드 변경 확인**

- [ ] `L5AsyncUpgradeWatcher._process_batch()` 에서 기존 `enqueue(..., enqueue_source='l5')` 를 직접 호출하는가
- [ ] 생성된 job의 `enqueue_source` 가 `'l5'` 인가
- [ ] 동일 `(repo_root, relative_path)` 로 중복 enqueue 시 upsert로 중복 job이 생성되지 않는가

---

### [ ] Step 7+8: Config 추가 및 전달

**코드 변경 확인**

- [ ] `config_model.py` — 아래 3개 필드가 추가되었는가
  - [ ] `l5_async_quality_upgrade_enabled: bool = True`
  - [ ] `l5_async_quality_upgrade_batch_size: int = 50`
  - [ ] `l5_async_quality_upgrade_poll_interval_sec: float = 5.0`
- [ ] `config_fields.py` — 대응하는 `_ConfigField` 3개가 추가되었는가
  - [ ] `SARI_L5_ASYNC_QUALITY_UPGRADE_ENABLED`
  - [ ] `SARI_L5_ASYNC_QUALITY_UPGRADE_BATCH_SIZE`
  - [ ] `SARI_L5_ASYNC_QUALITY_UPGRADE_POLL_INTERVAL_SEC`
- [ ] `config_default_loader.py` — 3개 필드가 파싱되어 `cls()` 에 전달되는가
- [ ] `daemon_process.py` — `EventBus` 생성 + config 전달 + `shutdown()` 호출이 있는가

**환경변수 동작 확인**

- [ ] `SARI_L5_ASYNC_QUALITY_UPGRADE_ENABLED=false` 시 기존 handoff 경로로 복귀되는가

---

### [ ] Step 9: 테스트

**EventBus 테스트 확인**

- [ ] `tests/unit/core/test_event_bus.py` 파일이 존재하고 아래 테스트가 PASS인가
  - [ ] `test_subscribe_callback_receives_published_event`
  - [ ] `test_subscribe_queue_receives_published_event`
  - [ ] `test_subscribe_queue_multiple_event_types`
  - [ ] `test_publish_does_not_propagate_handler_exception`
  - [ ] `test_shutdown_sends_sentinel_to_all_queues`
  - [ ] `test_publish_after_shutdown_is_noop`

**L5AsyncUpgradeWatcher 테스트 확인**

- [ ] `tests/unit/l5/test_upgrade_watcher.py` 파일이 존재하고 아래 테스트가 PASS인가
  - [ ] `test_watcher_activates_on_lsp_warm_ready`
  - [ ] `test_watcher_processes_batch_on_l3_flush_after_activation`
  - [ ] `test_watcher_ignores_l3_flush_before_activation`
  - [ ] `test_watcher_processes_on_poll_timeout`
  - [ ] `test_watcher_stops_on_shutdown`
  - [ ] `test_trigger_startup_activates_and_wakes`
  - [ ] `test_process_batch_enqueues_needs_l5_files`
  - [ ] `test_watcher_handles_scan_and_watcher_files_uniformly`

**기존 테스트 수정 확인**

- [ ] `tests/unit/misc/test_batch17_performance_hardening.py` — `l5_async_quality_upgrade_enabled=True` 명시
- [ ] 전체 테스트 suite PASS: `pytest tests/unit/ -x -q`

---

### [ ] 시나리오 검증

**시나리오 1: 최초 인덱싱 (콜드 스타트)**

- [ ] fresh DB로 `sari pipeline perf run --cold-lsp-reset` 실행
- [ ] TPS가 현재 기준(~157)보다 향상되었는가 (목표: ~200+)
- [ ] L3 처리 진행 중에도 LSP warm 완료 후 L5 enqueue가 시작되는가 (이벤트 기반 즉시 반응)
- [ ] Phase 2 완료 후 L5 처리율이 ~100%에 근접하는가
  ```sql
  SELECT
      COUNT(*) AS total_needs_l5,
      SUM(CASE WHEN s.workspace_id IS NOT NULL THEN 1 ELSE 0 END) AS l5_done
  FROM tool_data_l4_normalized_symbols q
  JOIN collected_files_l1 f
    ON q.repo_root = f.repo_root
    AND q.relative_path = f.relative_path
    AND q.content_hash = f.content_hash
  LEFT JOIN tool_data_l5_semantics s
    ON q.workspace_id = s.workspace_id
    AND q.repo_root = s.repo_root
    AND q.relative_path = s.relative_path
    AND q.content_hash = s.content_hash
  WHERE q.needs_l5 = 1 AND f.is_deleted = 0;
  ```

**시나리오 2: 재기동, 파일 변경 없음**

- [ ] daemon 강제 종료(Phase 2 미완료 상태 시뮬레이션) 후 재기동
- [ ] 재기동 로그에 `"startup: N stale L5 files detected"` 메시지가 출력되는가
- [ ] `trigger_startup()` 후 합성 이벤트로 watcher가 즉시 처리를 시작하는가

**시나리오 3: 재기동, 파일 변경 있음**

- [ ] 일부 파일 수정 후 daemon 재기동
- [ ] 변경 파일의 새 content_hash에 대한 `tool_data_l4_normalized_symbols` 레코드가 생성되는가
- [ ] 이전 content_hash의 L5 레코드가 무효화되는가 (content_hash 불일치로 대상 제외)
- [ ] Phase 2가 새 content_hash 파일을 대상으로 L5 처리하는가

**시나리오 4: Watcher 단일 파일 변경 (EventBus 통합)**

- [ ] 파일 하나를 수정
- [ ] `file_enrich_queue` 에 `enqueue_source='watcher'` job이 생성되는가
- [ ] L3 완료 → L3 flush → `L3FlushCompleted` 이벤트 발행되는가
- [ ] L5AsyncUpgradeWatcher가 즉시 반응하여 `enqueue_source='l5'` job을 생성하는가
  ```sql
  SELECT enqueue_source, status FROM file_enrich_queue
  WHERE relative_path = '수정한_파일_경로'
  ORDER BY updated_at DESC LIMIT 5;
  ```
- [ ] L5 처리 완료 후 해당 파일의 `tool_data_l5_semantics` 레코드가 갱신되는가

---

### [ ] 성능 회귀 확인

- [ ] `sari pipeline perf run` 결과 TPS가 이전 대비 하락하지 않았는가
- [ ] `sari pipeline perf run --cold-lsp-reset` 결과 Phase 1 TPS 목표(~200+) 달성 여부
- [ ] Phase 2 실행 중 `sari status` 에서 L5 worker queue 적체가 없는가
- [ ] EventBus publish 오버헤드가 L3 flush 성능에 영향을 주지 않는가
