# Sari – 로컬 코드 검색/인덱싱 MCP 서버

Sari는 로컬에서 동작하는 코드 검색/인덱싱 MCP 서버입니다. 소스코드를 외부로 보내지 않고, 로컬에서 빠르게 검색하도록 설계되어 있습니다.

Sari is a local code search/indexing MCP server. It is designed to index and search locally without sending your source code outside your machine.

---

## 1. 소개 / Overview

### (KR)
Sari는 대규모 코드베이스를 로컬에서 인덱싱하고 빠르게 검색할 수 있도록 설계된 MCP 서버입니다. MCP stdio 연동을 기본으로 하며, 다중 워크스페이스 수집과 명확한 설정/데이터 경로 분리를 제공합니다.

### (EN)
Sari is an MCP server built for fast, local indexing and search across large codebases. It defaults to MCP stdio integration, supports multi-workspace collection, and keeps configuration and data paths clearly separated.

---

## 2. 설치 / Installation

### 2.1 (KR) 권장: venv 설치
Sari는 venv 환경에서 설치/실행하는 방식을 권장합니다.

```bash
uv venv .venv
source .venv/bin/activate
uv pip install sari
```

업데이트:
```bash
source .venv/bin/activate
uv pip install -U sari
```

### 2.1 (EN) Recommended: venv install
We recommend installing and running Sari inside a venv.

```bash
uv venv .venv
source .venv/bin/activate
uv pip install sari
```

Update:
```bash
source .venv/bin/activate
uv pip install -U sari
```

---

### 2.2 (KR) 전역 설치(선택)
전역 설치도 가능하지만 MCP 설정 경로가 꼬이기 쉬우므로 venv를 권장합니다.

옵션 A: uv tool
```bash
uv tool install sari
```

옵션 B: 시스템 설치
```bash
uv pip install --system sari
```

MCP 설정의 `command`는 **설치된 파이썬 경로**를 사용해야 합니다.

### 2.2 (EN) Global install (optional)
Global install is possible, but venv is safer for MCP path consistency.

Option A: uv tool
```bash
uv tool install sari
```

Option B: system install
```bash
uv pip install --system sari
```

For MCP configs, the `command` must point to the **Python executable from the chosen install**.

---

## 3. 실행 모드 / Runtime Modes

### 3.1 (KR) stdio (MCP 고정 운용)
stdio는 데몬 프록시로 동작합니다. stdio를 사용하려면 데몬이 필요합니다.

데몬 시작:
```bash
sari daemon start -d
```

stdio 실행:
```bash
python -m sari --transport stdio
```

### 3.1 (EN) stdio (MCP recommended)
stdio runs as a daemon proxy. You need the daemon for stdio.

Start daemon:
```bash
sari daemon start -d
```

Run stdio:
```bash
python -m sari --transport stdio
```

---

### 3.2 (KR) HTTP API
HTTP API 서버를 실행합니다.

```bash
python -m sari --transport http --http-api-port 47777
```

헬스 체크:
```bash
curl http://127.0.0.1:47777/health
```

### 3.2 (EN) HTTP API
Start the HTTP API server.

```bash
python -m sari --transport http --http-api-port 47777
```

Health check:
```bash
curl http://127.0.0.1:47777/health
```

---

## 4. MCP 클라이언트 설정 / MCP Client Setup

### 4.1 (KR) Gemini CLI
`~/.gemini/settings.json`
```json
{
  "mcpServers": {
    "sari": {
      "command": "/abs/path/to/.venv/bin/python",
      "args": ["-m", "sari", "--transport", "stdio"],
      "env": {
        "SARI_CONFIG": "/abs/path/to/workspace/.sari/config.json"
      }
    }
  }
}
```

### 4.1 (EN) Gemini CLI
`~/.gemini/settings.json`
```json
{
  "mcpServers": {
    "sari": {
      "command": "/abs/path/to/.venv/bin/python",
      "args": ["-m", "sari", "--transport", "stdio"],
      "env": {
        "SARI_CONFIG": "/abs/path/to/workspace/.sari/config.json"
      }
    }
  }
}
```

---

### 4.2 (KR) Codex CLI
`~/.codex/config.toml`
```toml
[mcp_servers.sari]
command = "/abs/path/to/.venv/bin/python"
args = ["-m", "sari", "--transport", "stdio"]
env = { SARI_CONFIG = "/abs/path/to/workspace/.sari/config.json" }
```

### 4.2 (EN) Codex CLI
`~/.codex/config.toml`
```toml
[mcp_servers.sari]
command = "/abs/path/to/.venv/bin/python"
args = ["-m", "sari", "--transport", "stdio"]
env = { SARI_CONFIG = "/abs/path/to/workspace/.sari/config.json" }
```

---

### 4.3 (KR) 기타 MCP 클라이언트/IDE
아래 템플릿을 MCP 설정에 맞게 사용하세요.

```json
{
  "command": "/abs/path/to/python",
  "args": ["-m", "sari", "--transport", "stdio"],
  "env": {
    "SARI_CONFIG": "/abs/path/to/workspace/.sari/config.json"
  }
}
```

### 4.3 (EN) Other MCP clients/IDEs
Use the following template in your MCP settings.

```json
{
  "command": "/abs/path/to/python",
  "args": ["-m", "sari", "--transport", "stdio"],
  "env": {
    "SARI_CONFIG": "/abs/path/to/workspace/.sari/config.json"
  }
}
```

---

## 5. 데이터/설치 경로 / Data & Install Paths

### (KR)
- 전역 DB: `~/.local/share/sari/index.db`
- 전역 레지스트리: `~/.local/share/sari/server.json`
- 로그: `~/.local/share/sari/logs`
- 워크스페이스 설정: `<workspace>/.sari/config.json` 또는 `<workspace>/sari.json`
- 전역 설정: `~/.config/sari/config.json`

설치 경로는 설치 방식에 따라 다릅니다.
- venv: `<workspace>/.venv/`
- uv tool: `~/.local/share/uv/tools/sari/` (실행 바이너리: `~/.local/bin/sari`)
- system install: 시스템 Python site-packages

### (EN)
- Global DB: `~/.local/share/sari/index.db`
- Global registry: `~/.local/share/sari/server.json`
- Logs: `~/.local/share/sari/logs`
- Workspace config: `<workspace>/.sari/config.json` or `<workspace>/sari.json`
- Global config: `~/.config/sari/config.json`

Install path depends on the chosen method.
- venv: `<workspace>/.venv/`
- uv tool: `~/.local/share/uv/tools/sari/` (binary: `~/.local/bin/sari`)
- system install: system Python site-packages

---

## 6. 다중 워크스페이스 / Multi-workspace

### 6.1 (KR) CLI
```bash
sari roots add /path/to/workspaceA
sari roots add /path/to/workspaceB
sari roots list
```

### 6.1 (EN) CLI
```bash
sari roots add /path/to/workspaceA
sari roots add /path/to/workspaceB
sari roots list
```

### 6.2 (KR) 설정 파일
```json
{
  "workspace_roots": [
    "/path/to/workspaceA",
    "/path/to/workspaceB"
  ]
}
```

### 6.2 (EN) Config file
```json
{
  "workspace_roots": [
    "/path/to/workspaceA",
    "/path/to/workspaceB"
  ]
}
```

주의/Note:
- 상위/하위가 중첩되는 경로는 피하세요.
- Avoid nested roots (parent/child) to prevent duplicate scans.

---

## 7. 설정 레퍼런스 / Configuration Reference

다음은 주요 설정값입니다.

| Key | Description (KR) | Description (EN) | Default |
| --- | --- | --- | --- |
| `workspace_root` | 기본 워크스페이스 루트 | Default workspace root | 현재 작업 디렉토리 |
| `workspace_roots` | 다중 워크스페이스 목록 | Multi-workspace roots | `[workspace_root]` |
| `db_path` | DB 파일 경로 | DB file path | `~/.local/share/sari/index.db` |
| `include_ext` | 인덱싱 확장자 | File extensions to index | `.py, .js, .ts, .java, ...` |
| `include_files` | 확장자와 무관한 추가 파일 | Extra files regardless of extension | `[]` |
| `exclude_dirs` | 제외 디렉토리 | Excluded directories | `.git, node_modules, .venv, ...` |
| `exclude_globs` | 제외 패턴 | Excluded glob patterns | `.venv*, venv*, env*, *.egg-info` |
| `max_depth` | 탐색 최대 깊이 | Max directory depth | `20` |
| `scan_interval_seconds` | 자동 스캔 주기 | Auto-scan interval (sec) | `180` |
| `store_content` | 원문 저장 여부 | Store file content | `true` |
| `http_api_host` | HTTP 호스트 | HTTP host | `127.0.0.1` |
| `http_api_port` | HTTP 포트 | HTTP port | `47777` |

---

## 8. call_graph 옵션 / call_graph Options

### (KR)
`call_graph`는 스코프/예산을 명시할수록 안정성과 정확도가 올라갑니다.

- `repo`: 레포 강제 스코프
- `root_ids`: 루트 ID 스코프
- `depth`: 기본 깊이 (default: 2)
- `max_nodes`, `max_edges`, `max_depth`, `max_time_ms`: 그래프 예산
- `sort`: `line` 또는 `name`
- `include_paths`/`exclude_paths`: glob 패턴 필터

권장 예시:
```json
{
  "symbol": "FooService",
  "repo": "my-repo",
  "depth": 2,
  "max_nodes": 300,
  "max_edges": 800,
  "max_time_ms": 1500
}
```

### (EN)
`call_graph` becomes more stable and accurate when you provide explicit scope and budgets.

- `repo`: force repository scope
- `root_ids`: root id scope
- `depth`: base depth (default: 2)
- `max_nodes`, `max_edges`, `max_depth`, `max_time_ms`: graph budgets
- `sort`: `line` or `name`
- `include_paths`/`exclude_paths`: glob filters

Recommended:
```json
{
  "symbol": "FooService",
  "repo": "my-repo",
  "depth": 2,
  "max_nodes": 300,
  "max_edges": 800,
  "max_time_ms": 1500
}
```

---

## 9. 업데이트 / Update

### (KR)
```bash
source .venv/bin/activate
uv pip install -U sari
```

### (EN)
```bash
source .venv/bin/activate
uv pip install -U sari
```

---

## 9. 트러블슈팅 / Troubleshooting

문제가 발생하면 `docs/TROUBLESHOOTING.md`를 확인하세요.

Please see `docs/TROUBLESHOOTING.md` when issues occur.
