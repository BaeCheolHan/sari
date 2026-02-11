# Any Typing Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce high-impact `Any` anti-pattern usage in MCP protocol and registry layers without behavior change.

**Architecture:** Introduce narrow JSON/type aliases and registry `TypedDict` models to replace unbounded `Any` in public/internal interfaces. Keep runtime semantics stable and prove with targeted regression tests.

**Tech Stack:** Python 3.10+, pytest, stdlib typing (`TypedDict`, `Mapping`, `Sequence`, `Callable`, `TypeAlias`)

---

### Task 1: MCP protocol typing narrowing

**Files:**
- Modify: `src/sari/mcp/tools/protocol.py`
- Test: `tests/test_mcp_utils.py`

**Step 1: Write failing test**
- Add tests that exercise `mcp_response` with JSON payload merge and `pack_error` field encoding for non-string scalar fields.

**Step 2: Run test to verify it fails**
- Run: `pytest tests/test_mcp_utils.py -q`

**Step 3: Write minimal implementation**
- Replace `Any`-based signatures with explicit aliases:
  - JSON scalar/value/object aliases
  - `pack_encode_*` accepts `object`
  - `pack_header` accepts `Mapping[str, object]`
  - `pack_error` code typed as `ErrorCode | str | int`
  - `mcp_response` json callback typed to JSON object alias

**Step 4: Run test to verify it passes**
- Run: `pytest tests/test_mcp_utils.py -q`

### Task 2: Server registry typing narrowing

**Files:**
- Modify: `src/sari/core/server_registry.py`
- Test: `tests/test_core_resilience_deep.py` (or dedicated new small test file)

**Step 1: Write failing test**
- Add regression test to verify `_safe_load` always normalizes to v2 keys and returns typed daemon/workspace mappings after migration.

**Step 2: Run test to verify it fails**
- Run: `pytest tests/test_core_resilience_deep.py -k registry -q`

**Step 3: Write minimal implementation**
- Define typed dicts for daemon/workspace/registry payload and replace broad `Dict[str, Any]` annotations in key methods.

**Step 4: Run test to verify it passes**
- Run: `pytest tests/test_core_resilience_deep.py -k registry -q`

### Task 3: Verification

**Files:**
- N/A

**Step 1: Run focused verification**
- Run: `pytest tests/test_mcp_utils.py tests/test_core_resilience_deep.py -q`

**Step 2: Run smoke verification**
- Run: `pytest tests/test_daemon_resolver.py tests/test_registry_tools_smoke_minimal.py -q`
