# SSOT Registry and Layer Decoupling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make server registry the single source of truth for endpoint resolution and reduce MCP-to-core runtime coupling by introducing clear boundary adapters.

**Architecture:** Keep `sari.core.server_registry.ServerRegistry` as authoritative endpoint state and move all endpoint resolution through a single resolver API. MCP/CLI layers consume core-provided boundary interfaces instead of directly orchestrating stateful runtime objects. Legacy `server.json` compatibility remains read-only behind an explicit compatibility gate.

**Tech Stack:** Python 3.10+, pytest, existing `sari.core` and `sari.mcp` modules, no new third-party dependencies.

---

## Execution Status (Updated: 2026-02-13)

### Completed
- Task 1-7 objectives are implemented and validated through repeated full-suite runs.
- Entry layer was decoupled into domain modules and registry-based dispatch.
- `main.py` was slimmed to bootstrap/dispatch-focused responsibilities.
- Layer boundary contracts were hardened with import restriction tests.
- SSOT/layer documentation was added in `docs/architecture/SSOT_LAYER_BOUNDARIES.md`.

### Additional Hardening Completed After Initial Plan
- `daemon.py` was progressively decomposed into focused modules while preserving external API compatibility:
  - `src/sari/mcp/cli/daemon_lifecycle.py`
  - `src/sari/mcp/cli/daemon_registry_ops.py`
  - `src/sari/mcp/cli/daemon_process_ops.py`
  - `src/sari/mcp/cli/daemon_startup_ops.py`
  - `src/sari/mcp/cli/daemon_orchestration_ops.py`
- `src/sari/mcp/cli/daemon.py` now primarily acts as a compatibility facade and DI wrapper layer.
- Facade boundary contract coverage added:
  - `tests/test_daemon_facade_contract.py`

### Key Commit Trail (Most Recent First)
- `2c87179` refactor(doctor): extract daemon and runtime checks with compatibility wrappers
- `b747eac` refactor(doctor): extract system and log checks into dedicated module
- `503478e` refactor(doctor): split db and engine checks into dedicated module
- `5aa3bf0` refactor(doctor): split recommendations and autofix workflow from main tool
- `6d278bb` refactor(http): share status payload helpers across sync and async servers
- `683380d` refactor(http): decouple dashboard rendering from server handlers
- `27ef626` refactor(async-http): reuse shared error feed helpers with legacy hook compatibility
- `9f8deb2` refactor(http): share workspace status builder across sync and async servers
- `105e80d` refactor(http): extract error feed helpers from http server handler
- `9a4b50f` test(daemon): add facade delegation contract coverage
- `38217b0` refactor(daemon): extract existing-daemon orchestration decisions
- `97daf47` refactor(daemon): extract startup environment and launch operations
- `ce3a44f` refactor(daemon): extract stop and process-control operations
- `45c1e8d` refactor(daemon): extract lifecycle and registry operations modules
- `a83820f` refactor(entry): harden layer contracts and split index/uninstall adapters
- `1258924` refactor(entry): replace command if-chain with dispatch registry
- `7505088` refactor(entry): add command context for shared path and io resolution
- `ea69422` refactor(main): extract bootstrap routing and transport parsing
- `04204bc` refactor(main): split entry command handlers by domain

### Verification Snapshot
- Latest full regression run: `pytest -q`
- Result: `784 passed, 1 skipped, 6 deselected`

---

### Task 1: Lock SSOT behavior with failing tests first

**Files:**
- Modify: `tests/test_ssot_registry_contracts.py`
- Modify: `tests/test_daemon_resolver.py`
- Create: `tests/test_http_endpoint_resolution_contract.py`

**Step 1: Write the failing tests**
1. In `tests/test_http_endpoint_resolution_contract.py`, add tests that assert endpoint resolution order is strictly: override > env override flag > registry > default.
2. Add a test that legacy `server.json` is ignored when `SARI_STRICT_SSOT=1`.
3. In `tests/test_ssot_registry_contracts.py`, add a regression test proving registry value wins even when legacy file exists and env is unset.
4. In `tests/test_daemon_resolver.py`, add a test for resolver status telemetry (`resolver_ok`, `error`) when registry read throws.

**Step 2: Run tests to verify failure**
Run: `pytest tests/test_http_endpoint_resolution_contract.py tests/test_ssot_registry_contracts.py tests/test_daemon_resolver.py -q`
Expected: FAIL due to missing strict-SSOT branch and unified resolver contract.

**Step 3: Commit**
Run:
```bash
git add tests/test_http_endpoint_resolution_contract.py tests/test_ssot_registry_contracts.py tests/test_daemon_resolver.py
git commit -m "test: add strict ssot endpoint resolution contracts"
```

### Task 2: Introduce core endpoint resolver as single API

**Files:**
- Create: `src/sari/core/endpoint_resolver.py`
- Modify: `src/sari/core/daemon_resolver.py`
- Modify: `src/sari/core/__init__.py`

**Step 1: Write minimal implementation after failing tests**
1. Add pure functions in `src/sari/core/endpoint_resolver.py`:
   - `resolve_http_endpoint(workspace_root: str | None, host_override: str | None = None, port_override: int | None = None) -> tuple[str, int]`
   - `resolve_daemon_endpoint(workspace_root: str | None, force_override: bool = False) -> tuple[str, int]`
2. Centralize precedence logic and strict-SSOT toggle (`SARI_STRICT_SSOT`).
3. Keep resolver status tracking in one place and expose read-only accessor.

**Step 2: Wire existing daemon resolver**
1. Make `src/sari/core/daemon_resolver.py` delegate to `core.endpoint_resolver`.
2. Keep current public signatures to avoid call-site breakage.

**Step 3: Run targeted tests**
Run: `pytest tests/test_http_endpoint_resolution_contract.py tests/test_daemon_resolver.py -q`
Expected: PASS.

**Step 4: Commit**
Run:
```bash
git add src/sari/core/endpoint_resolver.py src/sari/core/daemon_resolver.py src/sari/core/__init__.py
git commit -m "feat: add core endpoint resolver for ssot contract"
```

### Task 3: Remove CLI-level endpoint resolution duplication

**Files:**
- Modify: `src/sari/mcp/cli/http_client.py`
- Modify: `src/sari/mcp/cli/registry.py`
- Modify: `tests/test_ssot_registry_contracts.py`

**Step 1: Refactor CLI HTTP client to core resolver**
1. Replace local resolution branches in `get_http_host_port()` with `core.endpoint_resolver.resolve_http_endpoint()`.
2. Keep `host_override` / `port_override` behavior intact.

**Step 2: Isolate legacy loader behind compatibility gate**
1. In `src/sari/mcp/cli/registry.py`, keep `load_server_info()` but make it no-op when strict SSOT is enabled.
2. Add deprecation warning path (stderr/logger) when legacy is consumed.

**Step 3: Run tests**
Run: `pytest tests/test_ssot_registry_contracts.py tests/test_cli_extra.py tests/test_daemon_resolver.py -q`
Expected: PASS.

**Step 4: Commit**
Run:
```bash
git add src/sari/mcp/cli/http_client.py src/sari/mcp/cli/registry.py tests/test_ssot_registry_contracts.py
git commit -m "refactor: unify cli endpoint resolution under core ssot resolver"
```

### Task 4: Decouple MCP server from concrete workspace registry lifecycle

**Files:**
- Create: `src/sari/mcp/adapters/workspace_runtime.py`
- Modify: `src/sari/mcp/server.py`
- Modify: `src/sari/mcp/session.py`
- Create: `tests/test_mcp_workspace_runtime_adapter.py`

**Step 1: Write failing adapter contract test**
1. Add `tests/test_mcp_workspace_runtime_adapter.py` to assert MCP server depends on adapter protocol (`acquire`, `release`, `touch`) not `Registry.get_instance()`.

**Step 2: Implement adapter**
1. Create thin adapter around `sari.core.workspace_registry.Registry` in `src/sari/mcp/adapters/workspace_runtime.py`.
2. Inject adapter into `LocalSearchMCPServer` constructor with default factory for backward compatibility.

**Step 3: Refactor call sites**
1. Replace direct `self.registry = Registry.get_instance()` in `src/sari/mcp/server.py` with adapter usage.
2. Keep behavior parity for reference counting and release semantics.

**Step 4: Run tests**
Run: `pytest tests/test_mcp_workspace_runtime_adapter.py tests/test_daemon_session_deep.py tests/test_session_connection_id_e2e.py -q`
Expected: PASS.

**Step 5: Commit**
Run:
```bash
git add src/sari/mcp/adapters/workspace_runtime.py src/sari/mcp/server.py src/sari/mcp/session.py tests/test_mcp_workspace_runtime_adapter.py
git commit -m "refactor: introduce mcp workspace runtime adapter boundary"
```

### Task 5: Reduce legacy wrapper blast radius

**Files:**
- Modify: `src/sari/app/__init__.py`
- Modify: `src/sari/mcp/cli/__init__.py`
- Create: `tests/test_legacy_wrapper_compat.py`

**Step 1: Add failing compatibility tests**
1. Verify only explicit allowlisted legacy modules are exported.
2. Verify unresolved legacy imports fail with actionable error.

**Step 2: Implement scoped compatibility layer**
1. Replace blanket submodule registration in `app.__init__` with explicit map.
2. In CLI init, keep `legacy_cli` delegation only for commands not yet migrated.

**Step 3: Run tests**
Run: `pytest tests/test_legacy_wrapper_compat.py tests/test_cli_commands.py tests/test_core_main.py -q`
Expected: PASS.

**Step 4: Commit**
Run:
```bash
git add src/sari/app/__init__.py src/sari/mcp/cli/__init__.py tests/test_legacy_wrapper_compat.py
git commit -m "chore: scope legacy wrappers to explicit compatibility surface"
```

### Task 6: Strengthen architecture boundary contracts

**Files:**
- Modify: `tests/test_layer_boundary_contracts.py`
- Create: `tests/test_endpoint_resolver_boundary.py`
- Modify: `tests/test_architecture_isolation.py`

**Step 1: Add boundary tests**
1. Assert endpoint resolution is imported from `sari.core.endpoint_resolver` only.
2. Assert MCP layer does not parse registry file schema directly.
3. Keep existing `core -> mcp` prohibition unchanged.

**Step 2: Run tests**
Run: `pytest tests/test_layer_boundary_contracts.py tests/test_endpoint_resolver_boundary.py tests/test_architecture_isolation.py -q`
Expected: PASS.

**Step 3: Commit**
Run:
```bash
git add tests/test_layer_boundary_contracts.py tests/test_endpoint_resolver_boundary.py tests/test_architecture_isolation.py
git commit -m "test: harden layer boundary contracts for ssot resolver"
```

### Task 7: End-to-end verification and documentation

**Files:**
- Modify: `docs/reference/ARCHITECTURE.md`
- Modify: `docs/reference/ARCHITECTURE_SERVER.md`
- Modify: `README_KR.md`

**Step 1: Update docs**
1. Document strict SSOT mode and endpoint precedence.
2. Document new MCP adapter boundary and migration rule (no direct lifecycle management in protocol layer).

**Step 2: Run regression suites**
Run:
```bash
pytest tests/test_ssot_registry_contracts.py tests/test_daemon_resolver.py tests/test_layer_boundary_contracts.py tests/test_mcp_contract_drift_regression.py tests/test_workspace_registry_single_http.py -q
pytest -q
```
Expected: PASS for targeted suites; full suite green.

**Step 3: Final commit**
Run:
```bash
git add docs/reference/ARCHITECTURE.md docs/reference/ARCHITECTURE_SERVER.md README_KR.md
git commit -m "docs: document ssot resolver and layer decoupling architecture"
```
