# Bug Hunt + SSOT Layer Audit Findings (2026-02-12)

## Scope
- Worktree: `.worktrees/bughunt-ssot-layer-audit`
- Focus: post-refactor bug hunt + layer separation + SSOT integrity

## Implemented Changes
- `tests/test_layer_boundary_contracts.py`
  - Added AST-based boundary guard for `core -> mcp` imports.
  - Added debt-neutral scanner health test (synthetic fixture-based).
- `tests/test_ssot_registry_contracts.py`
  - Added registry-vs-legacy conflict contract tests.
- `src/sari/core/daemon_resolver.py`
  - Refactored to registry-first endpoint resolution path.
- `src/sari/mcp/cli/registry.py`
  - Hardened legacy `server.json` loader to avoid overriding registry endpoint.

## Verification Evidence

### Task 2 / Task 3 Targeted
- `uv run pytest -q tests/test_layer_boundary_contracts.py`
  - `2 passed`
- `uv run pytest -q tests/test_ssot_registry_contracts.py`
  - `2 passed`

### Regression batches
- `uv run pytest -q tests/test_architecture_isolation.py tests/test_architecture_modern.py tests/test_runtime_gates.py tests/test_layer_boundary_contracts.py tests/test_ssot_registry_contracts.py`
  - `18 passed`
- `uv run pytest -q tests/test_daemon.py tests/test_daemon_autostop_policy.py tests/test_daemon_status_list.py tests/test_daemon_stop_all.py tests/test_http_server_workspace_routing.py tests/test_workspace_registry_single_http.py`
  - `55 passed`
- `uv run pytest -q tests/test_cli_commands.py`
  - `13 passed`
- `uv run pytest -q tests/test_daemon_status_list.py`
  - `2 passed`
- `uv run pytest -q tests/test_doctor_self_healing.py -k policy_prefers_registry_http_endpoint`
  - `1 passed, 6 deselected`

## Risk Summary
- **Layer risk reduced**: new unapproved `core -> mcp` imports now fail tests.
- **SSOT risk reduced**: registry endpoint now wins over legacy `server.json` in conflict path.
- **Residual risk**: full-suite runtime is long and may include slow/hanging scenarios; full `pytest -q` green confirmation not yet captured in this run.

## Next recommended execution
1. Run full suite with per-test timeout and failure localization:
   - `uv run pytest -q --timeout=120 --maxfail=1`
2. If it hangs, bisect by directory:
   - `uv run pytest -q tests/test_*daemon*`
   - `uv run pytest -q tests/test_*mcp*`
   - `uv run pytest -q tests/test_*doctor*`
