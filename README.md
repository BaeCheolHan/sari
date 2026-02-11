# Sari - Local MCP Code Search + Daemon + Dashboard

[한국어 문서](README_KR.md)

Sari is a local-first MCP server for large codebases.
It provides:

- Fast local indexing/search
- Multi-workspace registration
- Daemon lifecycle management
- Web dashboard for runtime/index visibility

Your code stays on your machine.

## Quick Start

### 1. Install

Recommended:

```bash
uv tool install sari
```

Check install path:

```bash
which sari
```

### 2. Start daemon

```bash
sari daemon start -d
```

### 3. Register workspaces to index

```bash
sari roots add /absolute/path/to/repo-a
sari roots add /absolute/path/to/repo-b
sari roots list
```

### 4. Open dashboard

Default URL:

```text
http://127.0.0.1:47777/
```

The dashboard shows status, health, workspace list, and per-workspace indexed file counts.

### 5. Verify runtime

```bash
sari daemon status
sari status
```

## MCP Setup

Minimal MCP server configuration (recommended):

### Codex CLI (`~/.codex/config.toml`)

```toml
[mcp_servers.sari]
command = "sari"
args = ["--transport", "stdio", "--format", "pack"]
```

### Gemini CLI (`~/.gemini/settings.json`)

```json
{
  "mcpServers": {
    "sari": {
      "command": "sari",
      "args": ["--transport", "stdio", "--format", "pack"]
    }
  }
}
```

You can also generate host-specific config snippets:

```bash
sari --cmd install --host codex --print
sari --cmd install --host gemini --print
```

## Workspace Registration Model

Sari uses config roots (not per-CLI hardcoded workspace env).

Primary commands:

```bash
sari roots add /abs/path
sari roots remove /abs/path
sari roots list
```

Config location resolution:

1. `<workspace>/.sari/mcp-config.json` if present
2. Fallback: `~/.config/sari/config.json`

## Daemon Lifecycle (Current Policy)

- Sari runs a single daemon endpoint per target host/port.
- If version mismatch is detected, daemon is replaced on same endpoint.
- Daemon autostop is session-aware:
  - If at least one active client session exists, daemon stays alive.
  - If active sessions drop to 0, daemon stops after grace period.
- Orphan index workers are handled with two safety layers:
  - Worker self-termination when parent daemon is gone.
  - `sari daemon stop` orphan sweep.

Useful commands:

```bash
sari daemon start -d
sari daemon ensure
sari daemon status
sari daemon stop
sari daemon refresh
```

## Dashboard and HTTP Endpoints

When daemon HTTP is up, these endpoints are available:

- `/` : Dashboard UI
- `/health` : Liveness
- `/status` : Runtime/index/system summary
- `/workspaces` : Registered workspace state
- `/search?q=...&limit=...` : HTTP search
- `/rescan` : Trigger rescan
- `/health-report` : Extended diagnostics

Example:

```bash
curl "http://127.0.0.1:47777/status"
curl "http://127.0.0.1:47777/workspaces"
```

## Common Operations

### Force rescan

```bash
sari --cmd index
```

### Doctor

```bash
sari doctor
```

### Show active config

```bash
sari --cmd config show
```

## Data Paths

- DB: `~/.local/share/sari/index.db`
- Registry: `~/.local/share/sari/server.json`
- Logs: `~/.local/share/sari/logs/`
- Global config: `~/.config/sari/config.json`
- Workspace config: `<workspace>/.sari/mcp-config.json`

## Troubleshooting

### Dashboard shows CJK tokenizer dictionary error

If the environment was installed before the dependency update, install or upgrade:

```bash
uv pip install lindera-python-ipadic
```

### Port conflict

```bash
sari daemon stop
sari daemon start -d --daemon-port 47789
```

### CLI updated but daemon still old

```bash
sari daemon refresh
```

## Upgrade / Remove

```bash
uv tool upgrade sari
uv tool uninstall sari
```
