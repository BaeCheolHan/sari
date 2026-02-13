# SSOT Layer Boundaries

## Purpose
This document defines the current source-of-truth and dependency boundaries for `sari` runtime entry, core runtime, and MCP transport layers.

## SSOT
- Daemon and HTTP endpoint resolution SSOT lives in `sari.core.endpoint_resolver` and core registry state.
- Entry/CLI modules must consume resolver-facing APIs and must not create alternate endpoint precedence rules.

## Dependency Direction
- `entry_*` -> `core` (allowed through explicit command modules and context adapters)
- `entry_*` -> `mcp` (restricted to adapter modules only)
- `mcp` -> `core` (allowed)
- `core` -> `mcp` (forbidden by contract tests unless explicitly allowlisted)

## Entry Layer Rules
- Routing modules (`entry_commands.py`, `entry_bootstrap.py`, `main.py`) should stay thin.
- Domain behavior belongs in `entry_commands_<domain>.py` modules.
- Shared path and output behavior must use `entry_command_context.CommandContext`.

## Restricted Imports
- Direct `sari.mcp.*` imports from entry layer are restricted to:
  - `src/sari/entry_bootstrap.py`
  - `src/sari/entry_commands_doctor.py`
  - `src/sari/entry_commands_index.py`
  - `src/sari/entry_commands_legacy.py`
- This policy is enforced by `tests/test_layer_boundary_contracts.py`.

## Entry Command Ownership
- `entry_commands_install.py`: install host config wiring
- `entry_commands_roots.py`: config/roots operations
- `entry_commands_engine.py`: engine lifecycle/status actions
- `entry_commands_doctor.py`: doctor output bridge
- `entry_commands_index.py`: index rescan trigger bridge
- `entry_commands_legacy.py`: status/search legacy CLI dispatch bridge
- `entry_commands_uninstall.py`: uninstall command bridge
- `entry_commands.py`: registry-based dispatcher only

## Operational Note for daemon.py
`src/sari/mcp/cli/daemon.py` currently mixes lifecycle, registry resolution, process management, and CLI formatting in one module. It remains functional but is a high-complexity hotspot and should be split into focused modules (resolution, process control, reporting) in a follow-up refactor.

Current split progress:
- `src/sari/mcp/cli/daemon_lifecycle.py`: daemon lifecycle parameter/upgrade decisions
- `src/sari/mcp/cli/daemon_registry_ops.py`: registry and process-discovery helper operations
- `src/sari/mcp/cli/daemon_process_ops.py`: process termination and stop orchestration helpers
- `src/sari/mcp/cli/daemon_startup_ops.py`: startup environment and background/foreground launch helpers
- `src/sari/mcp/cli/daemon_orchestration_ops.py`: existing-daemon replacement decision and singleton orchestration
- `src/sari/mcp/cli/daemon.py`: compatibility facade and orchestration entrypoints

## HTTP Status/Feed SSOT Modules
- `src/sari/core/http_error_feed.py`: `/errors` payload assembly SSOT (sync/async shared).
- `src/sari/core/http_workspace_feed.py`: `/workspaces` status assembly SSOT (sync/async shared).
- `src/sari/core/http_status_payload.py`: `/status` runtime/performance/queue/orphan warning assembly helpers (sync/async shared).
- `src/sari/core/dashboard_html.py`: dashboard HTML/script generation SSOT (sync/async shared).

## Doctor Layer Decomposition
- `src/sari/mcp/tools/doctor.py`: orchestrator and compatibility wrapper surface.
- `src/sari/mcp/tools/doctor_common.py`: common result/parse safety helpers.
- `src/sari/mcp/tools/doctor_daemon_endpoint.py`: daemon/http endpoint resolution helpers.
- `src/sari/mcp/tools/doctor_checks_db.py`: DB and engine runtime checks.
- `src/sari/mcp/tools/doctor_checks_system.py`: system/network/log/disk checks.
- `src/sari/mcp/tools/doctor_checks_daemon.py`: daemon/policy/http runtime marker checks.
- `src/sari/mcp/tools/doctor_actions.py`: recommendations and auto-fix/rescan workflow.
