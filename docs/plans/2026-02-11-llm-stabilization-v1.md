# LLM Stabilization v1

## Goal
- Add an observation + control + guidance layer so LLM workflows avoid:
  - token explosion
  - meaningless repeated reads
  - wrong-file reads
  - goal drift
- Keep control points centralized on Unified Read (Phase 1).

## Non-Goals (v1)
- No chain-of-thought analysis/storage.
- No automatic patch generation.
- No external SAST/test integration in this layer.

---

## Core Decisions (Locked)

### Session Identity
- Session key priority:
  1. `session_id` present -> `ws:<workspace_hash>:sid:<session_id>`
  2. fallback -> `ws:<workspace_hash>:conn:<connection_id>`
- `connection_id` is server-issued UUID per MCP Session/connection.
- `workspace_hash`:
  - canonicalized `workspace_root` (`expanduser`, `realpath`, normalized slash)
  - `sha1(... )[:12]`
- `client_connection_id` (from headers/clients):
  - telemetry only
  - never used for runtime policy/session keys
- `STRICT_SESSION_ID` mode is supported but default is `OFF`.

### State Separation
- `RuntimeState`:
  - policy decision state
  - in-memory only
  - discarded on session end/TTL
- `AnalyticsState`:
  - reporting/tuning state
  - async queue -> batch writer only
  - no hot-path I/O

### Deterministic Policy
- All control decisions emit fixed reason codes in `meta.stabilization.reason_codes`.
- Messages are UX-only; policy logic is reason-code driven.
- v1 core reason enum:
  - `SEARCH_FIRST_REQUIRED`
  - `SEARCH_REF_REQUIRED`
  - `CANDIDATE_REF_REQUIRED`
  - `BUDGET_SOFT_LIMIT`
  - `BUDGET_HARD_LIMIT`
  - `LOW_RELEVANCE_OUTSIDE_TOPK`
  - `PREVIEW_DEGRADED`
- Extension reasons are allowed via registry (e.g. strict/session/range variants).

### Reason Registry Model
- Code enum is authoritative for typing/tests/policy.
- `reason_registry.json` is auxiliary metadata:
  - description
  - severity
  - recommended `next_calls` templates
- Registry must not override code-level semantics.

### Analytics Queue Policy
- Queue: bounded, `maxsize=2000`
- Overflow: `drop_newest`
- Enqueue: non-blocking only (`put_nowait`)
- Observability:
  - `drop_count_by_type[event_type]` required
  - optional debug `drop_count_by_reason`

---

## Stabilization Primitives (v1)

### 1) Session Metrics (Observation)
- Metrics:
  - `reads_count`, `reads_lines_total`, `reads_chars_total`
  - `search_count`
  - `read_after_search_ratio`
  - `avg_read_span`, `max_read_span`
  - `preview_degraded_count`
- Storage:
  - in-memory default
  - optional sqlite backend is opt-in placeholder

### 2) Read Budget Guard (Control)
- Default policy:
  - `max_reads_per_session = 25`
  - `max_total_read_lines = 2500`
  - `max_single_read_lines = 300`
  - `max_preview_chars = 12000`
- On exceed:
  - `SOFT_LIMIT`: reduce payload + guide to search
  - `HARD_LIMIT`: `BUDGET_EXCEEDED` + actionable hint (`reason_codes` include `BUDGET_HARD_LIMIT`)

### 3) Relevance Guard (Wrong-Target Prevention)
- Inputs:
  - read target (`path`/`symbol`)
  - recent search query + top-K paths
  - workspace roots/exclusion policy
- Heuristics:
  - warn when target is outside recent top-K
  - skip excluded paths (`vendor/`, `node_modules/`, `.git/`, `dist/`)
- Output:
  - soft guidance first with alternatives (no default hard block)

### 4) Auto-Aggregation (Context Pollution Prevention)
- Consecutive read outputs in a session are:
  - deduplicated
  - structurally compressed (no LLM summarization)
- Optional `context_bundle_id` is returned for bundle reuse.

---

## Search->Ref->Read Pipeline (Enforced v1)

- `search` issues stable `candidate_id` and/or `bundle_id`.
- `search` response includes `next_calls` with ready-to-run args.
- `read` default policy is `enforce`:
  - read without valid ref is blocked
  - reasons: `SEARCH_FIRST_REQUIRED` or `SEARCH_REF_REQUIRED`/`CANDIDATE_REF_REQUIRED`
- Allowed exceptions:
  1. precision read only: `path + start_line + end_line`
  2. search-issued `next_calls` / valid ref path
- Precision read cap:
  - `max_range_lines=200` hard cap (configurable, default 200)
  - if exceeded: block + propose windowed read / ref-based read via `next_calls`

---

## Integration Points

### Unified read hook (required)
- At `execute_read(...)` entry/exit:
  - update metrics
  - apply budget checks
  - apply relevance checks
  - apply truncation/degradation policy

### Unified search hook (recommended)
- At `execute_search(...)` completion:
  - store latest query + top-K paths in session
  - provide baseline for relevance guard
  - issue `candidate_id`/`bundle_id`
  - emit `next_calls`

---

## API / Tool Surface (v1)
- Do not add new tools.
- Extend `read` and `search` responses with `meta.stabilization`.

Example:
- `meta.stabilization = { budget_state, suggested_next_action, warnings[], reason_codes[], metrics_snapshot, next_calls[] }`

---

## Failure Modes & Policies
- Budget exceeded:
  - `code: BUDGET_EXCEEDED`
  - message: `"Read budget exceeded. Use search to narrow scope: ..."`
- Low relevance (soft):
  - `code: LOW_RELEVANCE` (soft signal in stabilization metadata)
  - message/hint: `"This target seems unrelated. Try ..."`
- Missing ref in enforced mode:
  - `code: SEARCH_REF_REQUIRED` or `CANDIDATE_REF_REQUIRED`
  - message/hint: `"Use search results (candidate/bundle ref) or precision read path+range."`
- Always return actionable hints.

---

## Tests (Must-have)
- budget soft/hard limit
- relevance guard hit/miss
- aggregation dedupe stable output
- metrics counting deterministic behavior
- search->candidate/bundle issuance deterministic
- read enforce gate: block without ref, allow precision read <= 200 lines
- reason codes deterministic and stable
- analytics queue overflow metrics (`drop_count_by_type`) deterministic

---

## Implementation Status (Current)
- Unified read v1: implemented (`read` with 4 modes and validation/routing)
- Stabilization primitives:
  - Session Metrics: implemented
  - Read Budget Guard: implemented
  - Relevance Guard: implemented (soft-first)
  - Auto-Aggregation: implemented (v1-lite)
- Response surface:
  - `read` and `search` include `meta.stabilization`
- Session identity:
  - `session_id` 우선 + `connection_id` 폴백 키 해석 구현
  - workspace-hash prefix 적용
- Search/ref pipeline:
  - `candidate_id`, `bundle_id`, `next_calls` 발급/반환 구현
  - read 기본 `enforce` gate + precision read 예외(200 lines) 구현
- Deterministic policy:
  - `reason_codes` enum + `reason_registry.json` 도입
  - read/search에서 `meta.stabilization.reason_codes` 반환
- Analytics:
  - bounded async queue (`maxsize=2000`, `drop_newest`) 구현
  - `drop_count_by_type` 관측 구현
- Verified by tests:
  - `tests/test_unified_read_stabilization_metrics.py`
  - `tests/test_unified_read_budget_guard.py`
  - `tests/test_unified_read_relevance_guard.py`
  - `tests/test_unified_read_aggregation.py`
  - `tests/test_session_key_resolver.py`
  - `tests/test_search_ref_pipeline.py`
  - `tests/test_read_enforce_gate.py`
  - `tests/test_stabilization_reason_codes.py`
  - `tests/test_stabilization_analytics_queue.py`

---

## References
- `docs/plans/2026-02-11-unified-read-v1-design.md`
- `docs/plans/2026-02-11-unified-read-v1-implementation-plan.md`
- `docs/plans/2026-02-11-unified-read-v1-stabilization-checklist.md`
