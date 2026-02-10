# Sari – Local Code Search/Indexing MCP Server

[한국어 설명(Korean)](README_KR.md)

Sari is a local code search/indexing MCP server designed for fast, local indexing and search across large codebases. It defaults to MCP stdio integration, supports multi-workspace collection, and keeps configuration and data paths clearly separated.

---

## 1. Overview
Sari indexes large codebases locally and provides fast search capabilities via the Model Context Protocol (MCP). It ensures your source code stays on your machine while providing powerful search, symbol navigation, and call graph analysis to LLMs.

---

## 2. Installation

### 2.1 Recommended: uv tool (Global-like Utility)
Since Sari is an MCP server utility used across multiple projects, we strongly recommend using `uv tool`. It keeps the environment isolated while providing a **stable, single binary path** for all your workspaces.

*   **Pros**: Prevents redundant installations, stable `command` path for MCP config, and easy CLI access.

```bash
uv tool install sari
```

After installation, find the **absolute path** for your MCP settings:
```bash
which sari
# Example: /Users/yourname/.local/bin/sari
```

### 2.2 Optional: venv install (Per-project Isolation)
Use this only if you want to lock Sari to a specific project. Note that you'll need to install it in every workspace, and you must update your MCP `command` path whenever you switch projects.

```bash
uv venv .venv
source .venv/bin/activate
uv pip install sari
```

---

## 3. Runtime Modes

### 3.1 stdio (MCP Recommended)
The `stdio` transport runs as a daemon proxy for high performance.

Start the daemon (required for stdio):
```bash
sari daemon start -d
```

### 3.2 HTTP API
Start the HTTP API server for access via browser or other HTTP clients.

```bash
sari --transport http --http-api-port 47777
```

Health check:
```bash
curl http://127.0.0.1:47777/health
```

---

## 4. MCP Client Setup

Configuration depends on your installation method. Replace `yourname` with your actual system username.

### 4.1 Gemini CLI (~/.gemini/settings.json)

**A. If installed via `uv tool` (Recommended)**
```json
{
  "mcpServers": {
    "sari": {
      "command": "/Users/yourname/.local/bin/sari",
      "args": ["--transport", "stdio"],
      "env": {
        "SARI_CONFIG": "/abs/path/to/workspace/.sari/config.json"
      }
    }
  }
}
```

**B. If installed via `venv`**
```json
{
  "mcpServers": {
    "sari": {
      "command": "/abs/path/to/project/.venv/bin/python",
      "args": ["-m", "sari", "--transport", "stdio"],
      "env": {
        "SARI_CONFIG": "/abs/path/to/workspace/.sari/config.json"
      }
    }
  }
}
```

---

### 4.2 Codex CLI (~/.codex/config.toml)

**A. If installed via `uv tool` (Recommended)**
```toml
[mcp_servers.sari]
command = "/Users/yourname/.local/bin/sari"
args = ["--transport", "stdio"]

[mcp_servers.sari.env]
SARI_CONFIG = "/abs/path/to/workspace/.sari/config.json"
```

**B. If installed via `venv`**
```toml
[mcp_servers.sari]
command = "/abs/path/to/project/.venv/bin/python"
args = ["-m", "sari", "--transport", "stdio"]

[mcp_servers.sari.env]
SARI_CONFIG = "/abs/path/to/workspace/.sari/config.json"
```

---

## 5. Data & Install Paths

- **Global DB**: `~/.local/share/sari/index.db`
- **Global Registry**: `~/.local/share/sari/server.json`
- **Logs**: `~/.local/share/sari/logs`
- **Workspace Config**: `<workspace>/.sari/config.json` or `<workspace>/sari.json`
- **Global Config**: `~/.config/sari/config.json`

---

## 6. Multi-workspace

### 6.1 CLI
```bash
sari roots add /path/to/workspaceA
sari roots add /path/to/workspaceB
sari roots list
```

### 6.2 Config file
```json
{
  "workspace_roots": [
    "/path/to/workspaceA",
    "/path/to/workspaceB"
  ]
}
```

### 6.3 Collection Example
Register multiple workspaces in the global config and run.

```bash
# Register roots
sari roots add /Users/user/Repo1
sari roots add /Users/user/Repo2

# Run with global config
SARI_CONFIG=~/.config/sari/config.json sari --transport stdio
```

---

## 7. Indexing Policy

Sari determines indexing roots in this order:
1. `workspace_roots` from global/workspace config
2. `SARI_WORKSPACE_ROOT` environment variable
3. `rootUri/rootPath` from MCP initialization
4. Fallback to **current working directory (CWD)**

---

## 8. Configuration Reference

| Key | Description | Default |
| --- | --- | --- |
| `workspace_roots` | Multi-workspace roots | `[CWD]` |
| `include_ext` | File extensions to index | `.py, .js, .ts, .java, ...` |
| `exclude_dirs` | Excluded directories | `.git, node_modules, .venv, ...` |
| `max_depth` | Max directory depth | `20` |
| `scan_interval_seconds` | Auto-scan interval (sec) | `180` |

---

## 9. Call Graph Options
`call_graph` becomes more stable when you provide explicit scope:
- `repo`: force repository scope
- `depth`: search depth (default: 2)
- `include_paths`/`exclude_paths`: glob filters

---



## 10. Troubleshooting

Please see `docs/TROUBLESHOOTING.md` when issues occur.



---



## 11. Maintenance



### Update

- Force update from local source:

  ```bash

  uv tool install . --force

  ```

- Upgrade to the latest version via PyPI:

  ```bash

  uv tool upgrade sari

  ```



### Uninstall

```bash

uv tool uninstall sari

```
