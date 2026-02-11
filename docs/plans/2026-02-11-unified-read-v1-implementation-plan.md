# Unified Read v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship a single `read` tool with four modes (`file`, `symbol`, `snippet`, `diff_preview`) and add a stabilization layer (metrics/budget/relevance/aggregation) without adding new MCP tools.

**Architecture:** A unified dispatcher `execute_read(...)` becomes the single control point. Legacy read-like tools are wrappers into the new dispatcher. Stabilization runs as hooks at read/search boundaries and is exposed only through `meta.stabilization`.

**Tech Stack:** Python MCP tool layer, existing DB/search modules, in-memory session state (+ optional sqlite opt-in), pytest.

Related docs:
- `docs/plans/2026-02-11-unified-read-v1-design.md`
- `docs/plans/2026-02-11-read-unification-against-decision.md`

---

### Task 1: Registry & Unified Schema

**Files:**
- Modify: `src/sari/mcp/tools/registry.py`
- Modify: `src/sari/mcp/tools/read.py` (create if missing)
- Test: `tests/test_mcp_tools_extra.py`

**Steps:**
1. Register `read` as first-class entrypoint with `mode=file|symbol|snippet|diff_preview`.
2. Keep legacy tools (`read_file`, `read_symbol`, `get_snippet`, `dry_run_diff`) registered.
3. Ensure schema includes v1 constraints and guidance text for invalid combinations.
4. Add/adjust schema tests for accepted/rejected payloads.

---

### Task 2: Unified Validation & Routing

**Files:**
- Modify: `src/sari/mcp/tools/read.py`
- Modify: `src/sari/mcp/tools/read_file.py`
- Modify: `src/sari/mcp/tools/read_symbol.py`
- Modify: `src/sari/mcp/tools/get_snippet.py`
- Modify: `src/sari/mcp/tools/dry_run_diff.py`
- Test:
  - `tests/test_mcp_tools_extra.py`
  - `tests/test_low_coverage_mcp_tools_additional.py`

**Steps:**
1. Implement `execute_read(args, db, roots, ...)` dispatcher.
2. Enforce mode-specific args:
   - `against` only for `diff_preview`
   - `start_line/end_line/context_lines` only for `snippet`
   - `path` disambiguation only for `symbol`
3. Restrict `against` to `HEAD|WORKTREE|INDEX`.
4. Normalize response contract: `ok`, `mode`, `target`, `meta`, `text/content`, optional `location`.
5. Add invalid-combination tests with explicit correction guidance.

---

### Task 3: Session Metrics (Stabilization Primitive #1)

**Files:**
- Create/Modify: `src/sari/mcp/stabilization/session_state.py`
- Modify: `src/sari/mcp/tools/read.py`
- Modify: `src/sari/mcp/tools/search.py`
- Test:
  - `tests/test_unified_read_stabilization_metrics.py` (new)

**Steps:**
1. Add per-session metrics counters:
   - `reads_count`, `reads_lines_total`, `reads_chars_total`
   - `search_count`
   - `read_after_search_ratio`
   - `avg_read_span`, `max_read_span`
   - `preview_degraded_count`
2. Wire read entry/exit updates in `execute_read(...)`.
3. Wire search completion updates in `execute_search(...)`.
4. Keep in-memory as default.
5. Add optional sqlite persistence flag and no-op path when disabled.
6. Add deterministic unit tests for counting and ratio calculations.

---

### Task 4: Read Budget Guard (Stabilization Primitive #2)

**Files:**
- Create/Modify: `src/sari/mcp/stabilization/budget_guard.py`
- Modify: `src/sari/mcp/tools/read.py`
- Test:
  - `tests/test_unified_read_token_budget.py` (new or extend)

**Steps:**
1. Implement default limits:
   - `max_reads_per_session=25`
   - `max_total_read_lines=2500`
   - `max_single_read_lines=300`
   - `max_preview_chars=12000`
2. Implement `SOFT_LIMIT` behavior: degrade payload + attach guidance.
3. Implement `HARD_LIMIT` behavior: return `BUDGET_EXCEEDED`.
4. Include actionable hint: "Use search to narrow scope".
5. Add deterministic tests for soft/hard transitions.

---

### Task 5: Relevance Guard (Stabilization Primitive #3)

**Files:**
- Create/Modify: `src/sari/mcp/stabilization/relevance_guard.py`
- Modify: `src/sari/mcp/tools/read.py`
- Modify: `src/sari/mcp/tools/search.py`
- Test:
  - `tests/test_unified_read_relevance_guard.py` (new)

**Steps:**
1. Store latest search query + top-K paths in session state.
2. Compare incoming read target against recent search candidates.
3. Apply path-exclude heuristics (`vendor/`, `node_modules/`, `.git/`, `dist/`).
4. v1 default: return soft warning (`LOW_RELEVANCE`) + alternatives.
5. Keep hard-block behavior behind future policy flag (not default).
6. Add hit/miss tests with stable alternative suggestions.

---

### Task 6: Auto-Aggregation v1-lite (Stabilization Primitive #4)

**Files:**
- Create/Modify: `src/sari/mcp/stabilization/aggregation.py`
- Modify: `src/sari/mcp/tools/read.py`
- Test:
  - `tests/test_unified_read_aggregation.py` (new)

**Steps:**
1. Aggregate consecutive reads within session.
2. Perform deterministic deduplication and structural compression only.
3. Keep `context_bundle_id` optional/experimental in v1.
4. Ensure aggregation never mutates core payload semantics.
5. Add deterministic-output tests (same sequence => same bundle output).

---

### Task 7: Response Contract Extension (`meta.stabilization`)

**Files:**
- Modify: `src/sari/mcp/tools/read.py`
- Modify: `src/sari/mcp/tools/search.py`
- Test:
  - `tests/test_mcp_contract_drift_regression.py`
  - `tests/test_unified_read_stabilization_metrics.py`

**Steps:**
1. Add `meta.stabilization` without introducing new tools.
2. Suggested shape:
   - `budget_state`
   - `suggested_next_action`
   - `warnings[]`
   - `metrics_snapshot`
3. Preserve backward compatibility for existing consumers.
4. Add contract regression tests for new meta fields.

---

### Task 8: Legacy Wrappers & Deprecation

**Files:**
- Modify:
  - `src/sari/mcp/tools/read_file.py`
  - `src/sari/mcp/tools/read_symbol.py`
  - `src/sari/mcp/tools/get_snippet.py`
  - `src/sari/mcp/tools/dry_run_diff.py`
  - `src/sari/mcp/tools/registry.py`
- Test: existing legacy tool tests must remain green

**Steps:**
1. Convert legacy handlers to thin calls into unified `read`.
2. Preserve legacy response shape where required by tests.
3. Mark legacy tools `deprecated` and optionally `hidden` after validation.

---

### Task 9: Verification Gate

**Run (required):**
1. `python3 -m ruff check src tests`
2. Targeted tests:
   - `pytest -q tests/test_mcp_tools_extra.py`
   - `pytest -q tests/test_low_coverage_mcp_tools_additional.py`
   - `pytest -q tests/test_policy_intelligence.py`
   - `pytest -q tests/test_unified_read_token_budget.py`
   - `pytest -q tests/test_unified_read_stabilization_metrics.py`
   - `pytest -q tests/test_unified_read_relevance_guard.py`
   - `pytest -q tests/test_unified_read_aggregation.py`
3. Full regression:
   - `pytest -q`

**Acceptance Criteria:**
- New `read` modes validate and route correctly.
- `diff_preview` supports only `HEAD|WORKTREE|INDEX`.
- `meta.stabilization` is present and deterministic where applicable.
- Budget soft/hard policies behave as specified.
- Relevance guard provides actionable alternatives.
- Aggregation dedupe/compression is deterministic.
- No regressions in legacy read-like entrypoints.

---

## Execution Checklist

- [ ] Task 1 complete: registry + schema validated
- [ ] Task 2 complete: unified validation/routing + invalid-arg tests
- [ ] Task 3 complete: session metrics wired + deterministic tests
- [ ] Task 4 complete: budget guard soft/hard limits + tests
- [ ] Task 5 complete: relevance guard soft warnings + alternatives + tests
- [ ] Task 6 complete: aggregation v1-lite + deterministic tests
- [ ] Task 7 complete: `meta.stabilization` contract regression green
- [ ] Task 8 complete: legacy wrappers preserved + deprecation flags
- [ ] Task 9 complete: lint + targeted tests + full regression green
- [ ] Final doc sync check between design and implementation plan
