# Single HTTP Gateway Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run exactly one HTTP API server per daemon process and route all workspace requests through that single gateway, eliminating per-workspace HTTP port fragmentation.

**Architecture:** Keep the current daemon single-instance model and workspace session model, but decouple HTTP server lifecycle from `SharedState`. Introduce a daemon-level HTTP gateway bound once, then resolve per-request workspace context via explicit workspace selector and/or registry fallback. Preserve backward compatibility for clients that currently resolve HTTP host/port from registry.

**Tech Stack:** Python 3.13, `ThreadingHTTPServer`, existing MCP JSON-RPC tools, `ServerRegistry` SSOT, pytest.

---

## Design Alternatives

1. **Recommended: Daemon-global single HTTP gateway + per-request workspace routing**
- One HTTP server per daemon (`boot_id` scope), one stable host/port.
- Workspace routing inside handler by `workspace_root` query/header/body field.
- Keep `Registry.get_or_create(workspace_root)` for DB/indexer lifecycle only.
- Best operational simplicity and removes port drift completely.

2. **HTTP reverse proxy in front of per-workspace HTTP servers**
- Keep current per-workspace servers, add one front proxy.
- Less invasive to existing internals, but still keeps many hidden servers/process resources.
- Does not eliminate core complexity; only masks it.

3. **Hybrid (single default + optional dedicated workspace HTTP)**
- Gateway by default, dedicated ports only on explicit opt-in.
- Flexible but increases policy complexity and test matrix.

**Selected:** Option 1.

---

### Task 1: Capture Current Behavior With Failing Tests

**Files:**
- Modify: `tests/test_embedded_server.py`
- Create: `tests/test_single_http_gateway.py`

**Step 1: Write failing test for single HTTP binding per daemon**
- Add test that initializes two different workspaces through one daemon and asserts only one active HTTP endpoint is exposed/used.

**Step 2: Write failing test for workspace-routed status**
- Add test calling `/status?workspace_root=<path>` for two workspaces and assert different workspace-scoped roots are returned while host/port remain same.

**Step 3: Run test to verify RED**
Run: `uv run pytest -q tests/test_single_http_gateway.py`
Expected: FAIL because current implementation binds per-workspace HTTP servers.

**Step 4: Commit failing tests**
```bash
git add tests/test_single_http_gateway.py tests/test_embedded_server.py
git commit -m "test: define single-http-gateway behavior"
```

---

### Task 2: Move HTTP Server Lifecycle To Daemon Scope

**Files:**
- Modify: `src/sari/mcp/daemon.py`
- Modify: `src/sari/mcp/workspace_registry.py`
- Modify: `src/sari/core/http_server.py`

**Step 1: Add daemon-owned HTTP startup path**
- In `daemon.py`, start HTTP server once during daemon boot, store `self.httpd`, `self.http_host`, `self.http_port`.
- Register daemon-global HTTP endpoint in registry (see Task 3).

**Step 2: Remove per-workspace HTTP start/stop side effects**
- In `workspace_registry.py`, remove `serve_forever` invocation from `SharedState.start`.
- Keep watcher/indexer/session lifecycle unchanged.

**Step 3: Add workspace selector resolution in HTTP handler**
- In `http_server.py`, resolve target workspace using order:
  1) explicit request selector (`workspace_root` query param),
  2) header override (optional),
  3) request-bound default from daemon root.
- Use `Registry.get_or_create(target_workspace)` to access correct session data for status/search/health calls.

**Step 4: Run focused tests**
Run: `uv run pytest -q tests/test_single_http_gateway.py tests/test_embedded_server.py`
Expected: PASS.

**Step 5: Commit**
```bash
git add src/sari/mcp/daemon.py src/sari/mcp/workspace_registry.py src/sari/core/http_server.py tests/test_single_http_gateway.py tests/test_embedded_server.py
git commit -m "feat: make HTTP API daemon-global single gateway"
```

---

### Task 3: Registry Schema Update (HTTP endpoint ownership)

**Files:**
- Modify: `src/sari/core/server_registry.py`
- Modify: `src/sari/mcp/session.py`
- Modify: `src/sari/mcp/cli/http_client.py`
- Modify: `src/sari/mcp/cli/mcp_client.py` (if needed for ensure path)

**Step 1: Introduce daemon-level HTTP fields**
- Add/update daemon payload fields: `http_host`, `http_port`, `http_pid` under `daemons[boot_id]`.
- Keep workspace entries lightweight (`boot_id`, `last_active_ts`) for routing metadata.

**Step 2: Update resolver helpers**
- Add helper `resolve_workspace_http(workspace_root)` that resolves workspace -> boot_id -> daemon HTTP endpoint.
- Keep backward-compatible fallback to existing workspace-level `http_port` for migration period.

**Step 3: Update client host/port resolution**
- `http_client.get_http_host_port()` should prefer new daemon-owned endpoint via workspace->boot_id mapping.
- Preserve env/override priority semantics.

**Step 4: Add migration-safe behavior**
- If old records exist, auto-heal registry on write or read path (no hard fail).

**Step 5: Run tests**
Run: `uv run pytest -q tests/test_daemon_status_list.py tests/test_cli_extra.py tests/test_embedded_server.py`
Expected: PASS.

**Step 6: Commit**
```bash
git add src/sari/core/server_registry.py src/sari/mcp/session.py src/sari/mcp/cli/http_client.py src/sari/mcp/cli/mcp_client.py
git commit -m "refactor: move HTTP endpoint ownership to daemon registry"
```

---

### Task 4: Health/Doctor Routing Consistency On Gateway

**Files:**
- Modify: `src/sari/core/http_server.py`
- Modify: `src/sari/mcp/tools/doctor.py`
- Modify: `src/sari/core/health.py`
- Modify: `tests/test_dashboard_health_fallback.py`
- Create: `tests/test_http_health_routing.py`

**Step 1: Ensure `/health-report` always runs against selected workspace**
- Pass explicit `roots=[workspace_root]` based on selector; never rely on implicit `indexer.cfg.workspace_roots[0]` only.

**Step 2: Keep fallback output shape compatible**
- Ensure dashboard receives stable JSON structure even under tool errors.

**Step 3: Add routing test**
- Verify two workspaces via same HTTP endpoint return workspace-correct health payload.

**Step 4: Run tests**
Run: `uv run pytest -q tests/test_http_health_routing.py tests/test_dashboard_health_fallback.py`
Expected: PASS.

**Step 5: Commit**
```bash
git add src/sari/core/http_server.py src/sari/mcp/tools/doctor.py src/sari/core/health.py tests/test_http_health_routing.py tests/test_dashboard_health_fallback.py
git commit -m "fix: route health report by workspace on single gateway"
```

---

### Task 5: API Contract, Docs, and Backward Compatibility

**Files:**
- Modify: `README.md`
- Modify: `README_KR.md`
- Modify: `src/sari/docs/reference/ENVIRONMENT.md`
- Modify: `src/sari/docs/TROUBLESHOOTING.md` (if exists)

**Step 1: Document single HTTP model**
- Clarify: one daemon HTTP endpoint, multiple workspace sessions routed by selector.

**Step 2: Document request selector**
- Add examples: `/status?workspace_root=/abs/path`.

**Step 3: Document registry migration behavior**
- Explain old workspace `http_port` fields are tolerated and auto-updated.

**Step 4: Run doc sanity checks**
Run: `rg -n "http_port|single HTTP|workspace_root" README.md README_KR.md src/sari/docs/reference/ENVIRONMENT.md`
Expected: consistent terminology.

**Step 5: Commit**
```bash
git add README.md README_KR.md src/sari/docs/reference/ENVIRONMENT.md src/sari/docs/TROUBLESHOOTING.md
git commit -m "docs: describe daemon-global single HTTP gateway"
```

---

### Task 6: Full Verification and Release Readiness

**Files:**
- No code changes expected

**Step 1: Run full test suite**
Run: `uv run pytest -q`
Expected: all pass.

**Step 2: Run smoke checks**
Run:
```bash
uv run sari daemon status
uv run sari doctor
curl -s http://127.0.0.1:<gateway_port>/status
curl -s "http://127.0.0.1:<gateway_port>/status?workspace_root=/Users/.../repoA"
curl -s "http://127.0.0.1:<gateway_port>/status?workspace_root=/Users/.../repoB"
```
Expected: one endpoint, workspace-specific payloads.

**Step 3: Final commit for any small fixups**
```bash
git add -A
git commit -m "chore: finalize single-http-gateway rollout"
```

---

## Change Scope Summary

- **Core behavior change:** HTTP server ownership moves from workspace session to daemon.
- **Registry contract change:** HTTP endpoint stored/resolved at daemon scope, workspace maps to boot_id.
- **Client behavior change:** HTTP clients resolve shared endpoint, route workspace via selector.
- **Compatibility strategy:** tolerate old workspace-level `http_port` while migrating.
- **Primary risk:** incorrect workspace routing on shared endpoint.
- **Mitigation:** explicit routing tests + health/status dual-workspace smoke tests.
