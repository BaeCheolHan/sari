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

Configuration depends on your installation method and whether you want to manage workspaces globally or per-project.

### 4.1 Recommended: Global Configuration (Unified Search)
Use this if you want to search across multiple repositories from any workspace.

1.  **Initialize Global Config**:
    ```bash
    mkdir -p ~/.config/sari
    echo '{"workspace_roots": []}' > ~/.config/sari/config.json
    ```
2.  **Add Your Workspaces**:
    ```bash
    sari roots add /abs/path/to/repo1
    sari roots add /abs/path/to/repo2
    ```
3.  **Update MCP Settings**: Keep MCP config minimal. `sari` auto-resolves standard config paths.

#### **A. Gemini CLI (~/.gemini/settings.json)**
```json
{
  "mcpServers": {
    "sari": {
      "command": "sari",
      "args": ["--transport", "stdio"]
    }
  }
}
```

#### **B. Codex CLI (~/.codex/config.toml)**
```toml
[mcp_servers.sari]
command = "sari"
args = ["--transport", "stdio"]
```

> **Note**: Ensure that `sari` is available in your system `PATH`. If you installed via `uv tool`, this is usually handled automatically.

---

### 4.2 Optional: Per-project Configuration
Use this if you want isolated indexing for a specific workspace.

**A. Gemini CLI**
```json
{
  "mcpServers": {
    "sari": {
      "command": "/Users/yourname/.local/bin/sari",
      "args": ["--transport", "stdio"]
    }
  }
}
```

**B. Codex CLI**
```toml
[mcp_servers.sari]
command = "/Users/yourname/.local/bin/sari"
args = ["--transport", "stdio"]
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
sari --transport stdio
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
