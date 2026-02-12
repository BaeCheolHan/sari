# Policy Engine Unification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Centralize runtime policy decisions (gate mode, read/snippet/preview caps, daemon TTL/grace/deadline, daemon status-env parsing) into one policy engine module and convert callers to apply-only logic.

**Architecture:** Add `src/sari/core/policy_engine.py` as the single source of truth with typed dataclasses and pure helper functions. Existing modules call this engine to fetch policy values and parsed daemon status markers, removing local env/config branching. Keep behavior-compatible defaults while preserving current stabilization tests.

**Tech Stack:** Python 3.10+, pytest, existing `sari.core.settings`, MCP stabilization modules.

---

### Task 1: Write failing policy-engine contract tests

**Files:**
- Create: `tests/test_policy_engine.py`

1. Add tests for read policy defaults and env overrides (`gate_mode`, `max_range_lines`, preview/snippet caps).
2. Add tests for daemon policy defaults and overrides (`lease_ttl`, `autostop_grace`, `heartbeat`, `inhibit_max`).
3. Add tests for daemon status marker parsing from env payload.

### Task 2: Implement centralized policy engine

**Files:**
- Create: `src/sari/core/policy_engine.py`

1. Add `ReadPolicy`, `DaemonPolicy`, `DaemonRuntimeStatus` dataclasses.
2. Add `load_read_policy(settings_obj=None, environ=None)`.
3. Add `load_daemon_policy(settings_obj=None, environ=None)`.
4. Add `load_daemon_runtime_status(environ=None)` for status endpoint parsing.

### Task 3: Refactor read/budget/snippet/diff to consume policy engine

**Files:**
- Modify: `src/sari/mcp/tools/read.py`
- Modify: `src/sari/mcp/stabilization/budget_guard.py`
- Modify: `src/sari/mcp/tools/get_snippet.py`
- Modify: `src/sari/mcp/tools/dry_run_diff.py`

1. Replace local gate env parsing and range cap logic with `load_read_policy()`.
2. Make `BudgetPolicy` derive from `ReadPolicy` values.
3. Remove duplicated snippet/diff constants and fetch caps from policy engine.

### Task 4: Refactor daemon loop to consume daemon policy

**Files:**
- Modify: `src/sari/mcp/daemon.py`

1. Replace scattered `settings.get_int/get_bool` lookups with a single `daemon_policy = load_daemon_policy(settings)`.
2. Use that policy in heartbeat/controller decisions (`lease_ttl`, `autostop_grace`, `inhibit_max`, `heartbeat interval`).

### Task 5: Refactor sync/async status endpoints to consume parsed runtime status

**Files:**
- Modify: `src/sari/core/async_http_server.py`
- Modify: `src/sari/core/http_server.py`

1. Replace duplicate env parse blocks with `load_daemon_runtime_status()`.
2. Keep response keys unchanged to satisfy status contract tests.

### Task 6: Verify and lock regressions

**Files:**
- Existing tests + new tests

1. Run targeted suites for policy, read gate/budget, search ref pipeline, daemon autostop, sync/async status.
2. Run broader MCP/core regression batch already used for stabilization.
