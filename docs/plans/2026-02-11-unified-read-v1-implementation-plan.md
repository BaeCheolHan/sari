# Unified Read v1 Implementation Plan

> **For Claude/Other LLM:** Implement in small batches. Validate each task before moving on.

**Goal:** Ship a single `read` tool with four modes (`file`, `symbol`, `snippet`, `diff_preview`) while preserving backward compatibility via legacy wrappers.

**Architecture:** Facade dispatcher in `read.py` + mode-specific adapters to existing logic. Unified validation and response contract. Legacy tools become thin wrappers.

**Tech Stack:** Python MCP tool layer, existing DB/search tool modules, pytest.

---

### Task 1: Schema & Registry

**Files:**
- Modify: `src/sari/mcp/tools/registry.py`
- Modify: `src/sari/mcp/tools/read_file.py` (temporary wrapper point if needed)
- Test: `tests/test_mcp_tools_extra.py`

**Steps:**
1. Register/update `read` tool schema with `mode` enum:
   - `file|symbol|snippet|diff_preview`
2. Add mode-specific parameter descriptions and constraints in schema text.
3. Keep legacy tools registered for now.
4. Add/adjust tests that schema can accept expected `read` arguments.

---

### Task 2: Unified Dispatcher & Validation

**Files:**
- Modify: `src/sari/mcp/tools/read_file.py` (or create `src/sari/mcp/tools/read.py` and delegate)
- Modify: `src/sari/mcp/tools/read_symbol.py`
- Modify: `src/sari/mcp/tools/get_snippet.py`
- Modify: `src/sari/mcp/tools/dry_run_diff.py`
- Create/Modify tests:
  - `tests/test_mcp_tools_extra.py`
  - `tests/test_low_coverage_mcp_tools_additional.py`

**Steps:**
1. Implement `execute_read(args, db, roots, ...)` as unified entrypoint.
2. Add validation:
   - `against` only for `diff_preview`.
   - `start_line/end_line/context_lines` only for `snippet`.
   - symbol-specific disambiguation args only for `symbol`.
3. Error messages must include correction guidance.
4. Ensure `diff_preview` only accepts `HEAD|WORKTREE|INDEX`.
5. Add tests for invalid argument combinations.

---

### Task 3: Mode Routing

**Files:**
- Modify: same as Task 2
- Tests:
  - `tests/test_mcp_tools_extra.py`
  - `tests/test_policy_intelligence.py`

**Steps:**
1. Route `mode=file` to existing file-read behavior.
2. Route `mode=symbol` to existing symbol-read behavior.
3. Route `mode=snippet` to existing snippet-read behavior.
4. Route `mode=diff_preview` to a bounded `dry_run_diff` path.
5. Normalize outputs into one response contract:
   - `ok`, `mode`, `target`, `meta`, `content/text`.

---

### Task 4: Token Budget Guards

**Files:**
- Modify: unified read entrypoint module
- Tests:
  - `tests/test_low_coverage_mcp_tools_additional.py`
  - add dedicated test file if needed: `tests/test_unified_read_token_budget.py`

**Steps:**
1. Enforce `max_preview_chars` upper bounds.
2. Apply degradation when output would exceed budget.
3. Return `meta.preview_degraded=true` when degraded.
4. Keep deterministic truncation behavior for stable tests.

---

### Task 5: Legacy Wrappers & Deprecation

**Files:**
- Modify:
  - `src/sari/mcp/tools/read_file.py`
  - `src/sari/mcp/tools/read_symbol.py`
  - `src/sari/mcp/tools/get_snippet.py`
  - `src/sari/mcp/tools/dry_run_diff.py`
  - `src/sari/mcp/tools/registry.py`
- Tests:
  - existing legacy tool tests must remain green

**Steps:**
1. Convert legacy tools to thin wrapper calls into unified `read`.
2. Mark legacy tools `deprecated` and optionally `hidden`.
3. Keep legacy output shape stable where tests rely on it.

---

### Task 6: Verification Gate

**Run (required):**
1. `python3 -m ruff check src tests`
2. Targeted tests:
   - `pytest -q tests/test_mcp_tools_extra.py tests/test_low_coverage_mcp_tools_additional.py tests/test_policy_intelligence.py`
3. Full regression:
   - `pytest -q`

**Acceptance Criteria:**
- New `read` modes work and validate correctly.
- `diff_preview` supports only `HEAD|WORKTREE|INDEX`.
- No regression in existing read-related workflows.
- Token budget metadata behaves deterministically.
