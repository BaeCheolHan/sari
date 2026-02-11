# Env Removal + Autoscaler Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove CLI workspace env-based registration and autostart env dependency, then add daemon/indexer autoscaling behavior and visibility.

**Architecture:** Workspace roots are sourced only from registry/config data managed by SARI itself (not CLI env injection). Daemon startup policy becomes internal default behavior, while worker count is dynamically tuned from queue depth + system load and surfaced in status/UI.

**Tech Stack:** Python 3.13, FastAPI/HTTP server, sqlite runtime metadata, pytest.

---

### Task 1: Remove CLI workspace env injection

**Files:**
- Modify: `src/sari/mcp/cli/daemon.py`
- Modify: `src/sari/core/main.py`
- Test: `tests/test_daemon_autostop_policy.py`

1. Write/adjust tests asserting daemon behavior does not depend on `SARI_WORKSPACE_ROOT` and does not require `SARI_DAEMON_AUTOSTART`.
2. Run focused tests to confirm failures.
3. Remove env writes/reads in CLI and core entrypoints.
4. Re-run focused tests until green.

### Task 2: Remove daemon env dependency and define fixed startup policy

**Files:**
- Modify: `src/sari/mcp/daemon.py`
- Modify: `src/sari/core/settings.py`
- Test: `tests/test_daemon_autostop_policy.py`

1. Add failing tests for new policy.
2. Replace `_env_flag("SARI_DAEMON_AUTOSTART", ...)` path with internal policy.
3. Remove dead setting/env references if no longer used.
4. Re-run policy tests.

### Task 3: Add autoscaler and status exposure

**Files:**
- Modify: `src/sari/core/indexer/main.py`
- Modify: `src/sari/core/http_server.py`
- Modify: `src/sari/core/static/index.html`
- Test: `tests/test_http_server_workspace_routing.py`

1. Add failing tests for status payload exposing autoscaler metadata.
2. Implement dynamic worker tuning and telemetry.
3. Surface values in API and HTML (system section).
4. Re-run focused tests.

### Task 4: End-to-end verification and release readiness

**Files:**
- Modify: version/release files as needed after code verification.

1. Run lint (`ruff`) and targeted/full tests.
2. Validate daemon status + dashboard payload manually.
3. Prepare commit/release artifacts after all checks pass.
