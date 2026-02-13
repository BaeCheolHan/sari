# Blue/Green Daemon Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver zero-downtime daemon rollout with fixed ingress port and automatic rollback on unhealthy switch.

**Architecture:** Keep `127.0.0.1:47777` as a permanent router endpoint. Drive rollout with generation-based deployment state in registry, then switch active upstream atomically and drain old daemon.

**Tech Stack:** Python, filelock, existing ServerRegistry/daemon/http_server/cli stack, pytest.

---

### Task 1: Add deployment state to registry (Phase A)
**Files:**
- Modify: `src/sari/core/server_registry.py`
- Test: `tests/test_server_registry_deployment.py`

**Step 1: Write failing tests**
- Add tests for `begin_deploy`, `mark_candidate_healthy`, `switch_active`, `record_health_failure`, `rollback_active`.
- Add generation mismatch no-op test.

**Step 2: Run tests to confirm RED**
- Run: `uv run pytest tests/test_server_registry_deployment.py -q`

**Step 3: Implement minimal registry APIs**
- Add `deployment` block normalization/coercion.
- Implement generation-gated mutators.
- Keep backward compatibility with existing snapshot schema users.

**Step 4: Run tests to confirm GREEN**
- Run: `uv run pytest tests/test_server_registry_deployment.py -q`

**Step 5: Commit**
- `git add ... && git commit -m "feat(registry): add blue-green deployment state machine"`

### Task 2: Enforce lifecycle serialization (Phase A)
**Files:**
- Modify: `src/sari/mcp/cli/daemon_lifecycle_lock.py`
- Modify: `src/sari/mcp/cli/commands/daemon_commands.py`
- Test: `tests/test_daemon_lifecycle_lock.py`, `tests/test_cli_extra.py`

**Step 1: Write failing tests**
- Ensure `start/stop/refresh` acquire a shared lifecycle lock.
- Ensure lock timeout returns deterministic error.

**Step 2: Run tests (RED)**
- Run: `uv run pytest tests/test_daemon_lifecycle_lock.py tests/test_cli_extra.py -q`

**Step 3: Implement lock wrappers**
- Wrap lifecycle operations with one lock path.
- Keep refresh as a single critical section (no nested lock).

**Step 4: Run tests (GREEN)**
- Run: `uv run pytest tests/test_daemon_lifecycle_lock.py tests/test_cli_extra.py -q`

**Step 5: Commit**
- `git add ... && git commit -m "fix(cli): serialize daemon lifecycle operations"`

### Task 3: Build fixed-router active upstream switch (Phase B)
**Files:**
- Modify: `src/sari/core/http_server.py` (or new router module)
- Modify: `src/sari/core/endpoint_resolver.py`
- Modify: `src/sari/mcp/daemon.py`
- Test: new integration tests under `tests/`

**Step 1: Write failing integration tests**
- Verify ingress port remains fixed while active upstream changes.
- Verify old daemon drains after switch.

**Step 2: Implement router + active lookup**
- Router resolves active boot from registry deployment state.
- Proxy to active daemon endpoint only.

**Step 3: Run integration tests**
- Ensure no ingress port changes and no user-visible downtime.

**Step 4: Commit**
- `git add ... && git commit -m "feat(router): fixed ingress with active upstream switching"`

### Task 4: Auto-deploy and rollback policy (Phase C)
**Files:**
- Modify: `src/sari/mcp/cli/smart_daemon.py`
- Modify: `src/sari/mcp/cli/daemon_lifecycle.py`
- Modify: `src/sari/mcp/daemon.py`
- Test: new auto-switch/rollback tests

**Step 1: Write failing tests**
- Version mismatch starts candidate.
- Candidate healthy triggers switch.
- Post-switch 3 consecutive health failures trigger rollback.

**Step 2: Implement policy**
- Add deploy generation flow.
- Add failure streak updates.
- Add rollback transition with workspace mapping restore.

**Step 3: Run tests**
- Validate happy path and rollback path.

**Step 4: Commit**
- `git add ... && git commit -m "feat(deploy): automatic blue-green switch and rollback"`

### Task 5: Observability and release hardening
**Files:**
- Modify: `src/sari/core/http_status_payload.py`
- Modify: dashboard/doctor related modules
- Test: status payload tests and doctor tests

**Step 1: Add deployment telemetry fields**
- generation/state/active/candidate/fail-streak/rollback-reason.

**Step 2: Add regression tests**
- Assert fields are present and stable.

**Step 3: Full verification**
- Run: `uv run pytest tests/test_daemon_singleton_start.py tests/test_daemon_stability_new.py tests/test_daemon_stop_all.py tests/test_knowledge_tool.py tests/test_mcp_tools_extra.py tests/test_stability.py tests/test_architecture_modern.py tests/test_symbol_algorithms.py -q`
- Run: `uv run ruff check`

**Step 4: Commit and release**
- Tag and release after CI pass.
