# Unified Search v3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Integrate multiple fragmented search tools into a single, intelligent `search` entry point with auto-inference, waterfall logic, and normalized responses.

**Architecture:** A Facade pattern implementation where `execute_search` in `search.py` acts as a dispatcher. It uses a new `InferenceEngine` to determine intent, routes to existing logic, and applies a common `ResponseContract` layer for output consistency.

**Tech Stack:** Python, SQLite (FTS5), Tree-sitter, MCP Protocol.

---

### Task 1: Schema & Parameter Validation

**Files:**
- Modify: `sari/src/sari/mcp/tools/registry.py`
- Modify: `sari/src/sari/mcp/tools/search.py`
- Create: `sari/tests/mcp/tools/test_search_v3_validation.py`

**Step 1: Write validation tests for the new schema**
Write tests that verify:
- New parameters (`search_type`, `preview_mode`, etc.) are accepted.
- Mode-specific validation (e.g., `kinds` only for `symbol`) returns `INVALID_ARGS`.

**Step 2: Update Schema in `registry.py`**
Update the `search` tool registration with the comprehensive v3 JSON schema.

**Step 3: Implement Validation logic in `search.py`**
Add a validator function that checks parameter compatibility based on `search_type`.

**Step 4: Verify tests pass**
Run: `pytest sari/tests/mcp/tools/test_search_v3_validation.py`

---

### Task 2: Dispatcher & Inference Engine

**Files:**
- Modify: `sari/src/sari/mcp/tools/search.py`
- Create: `sari/src/sari/mcp/tools/inference.py`
- Create: `sari/tests/mcp/tools/test_inference.py`

**Step 1: Implement Inference Logic**
Create `inference.py` with `resolve_search_intent(query)` function implementing heuristics (symbol vs api vs code) and SQL security blocker.

**Step 2: Update `execute_search` Dispatcher**
Refactor `execute_search` to:
1. Call `resolve_search_intent` if `search_type='auto'`.
2. Route to specialized executors (imported from other tool modules or moved into a service layer).

**Step 3: Implement Waterfall fallback**
Add logic to try `symbol` first, then `code` if 0 results in `auto` mode.

**Step 4: Verify inference and routing**
Run: `pytest sari/tests/mcp/tools/test_inference.py`

---

### Task 3: Response Normalization & Token Budget

**Files:**
- Modify: `sari/src/sari/mcp/tools/search.py`
- Create: `sari/tests/mcp/tools/test_search_v3_response.py`

**Step 1: Implement Response Mapper**
Create a mapping layer that converts hits from different engines into the `matches` array with common fields (`type`, `path`, `identity`, `location`).

**Step 2: Implement Token Budget logic**
Add `PreviewManager` to handle `max_preview_chars` adjustment and `preview_degraded` metadata flag.

**Step 3: Update Output Builders**
Modify `build_json` and `build_pack` to use the normalized `matches` structure.

**Step 4: Verify response format and token management**
Run: `pytest sari/tests/mcp/tools/test_search_v3_response.py`

---

### Task 4: Legacy Tool Cleanup & Final Integration

**Files:**
- Modify: `sari/src/sari/mcp/tools/registry.py`
- Modify: `sari/src/sari/mcp/tools/search.py`

**Step 1: Deprecate old tools**
Mark `search_symbols`, `grep_and_read`, etc., as `deprecated=True` and `hidden=True` in `registry.py`.

**Step 2: Internal routing for backward compatibility**
Ensure old tool entry points now just call the new unified `search` logic internally.

**Step 3: Final End-to-End verification**
Run all existing search-related tests + new v3 tests.

**Step 4: Commit and finalize**
```bash
git add .
git commit -m "feat: implement unified search v3 with auto-inference and normalized responses"
```
