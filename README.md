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

### 2.2 (KR) 편리한 전역 설치: uv tool (추천)
가상환경을 수동으로 관리하기 번거롭다면 `uv tool`을 사용하세요. 실행 파일만 전역 경로에 연결해줍니다.

```bash
uv tool install sari
```

설치 후 다음 명령어로 **절대 경로**를 확인하세요 (MCP 설정에 필요).
```bash
which sari
# 예시: /Users/yourname/.local/bin/sari
```

### 2.2 (EN) Global-like: uv tool (Recommended)
Use `uv tool` for automatic management of the execution environment.

```bash
uv tool install sari
```

After installation, find the **absolute path** of the binary:
```bash
which sari
# Example: /Users/yourname/.local/bin/sari
```

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

### 4.1 Gemini CLI (~/.gemini/settings.json)

**A. `uv tool install` (Recommended)**
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

**B. `venv` install**
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

**A. `uv tool install` (Recommended)**
```toml
[mcp_servers.sari]
command = "/Users/yourname/.local/bin/sari"
args = ["--transport", "stdio"]

[mcp_servers.sari.env]
SARI_CONFIG = "/abs/path/to/workspace/.sari/config.json"
```

**B. `venv` install**
```toml
[mcp_servers.sari]
command = "/abs/path/to/project/.venv/bin/python"
args = ["-m", "sari", "--transport", "stdio"]

[mcp_servers.sari.env]
SARI_CONFIG = "/abs/path/to/workspace/.sari/config.json"
```

---

### 4.3 Other MCP clients/IDEs
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
- 전역 DB: `~/.local/share/sari/index.db` (단일 DB 고정)
- 전역 레지스트리: `~/.local/share/sari/server.json`
- 로그: `~/.local/share/sari/logs`
- 워크스페이스 설정: `<workspace>/.sari/config.json` 또는 `<workspace>/sari.json` (db_path는 무시됨)
- 전역 설정: `~/.config/sari/config.json`

설치 경로는 설치 방식에 따라 다릅니다.
- venv: `<workspace>/.venv/`
- uv tool: `~/.local/share/uv/tools/sari/` (실행 바이너리: `~/.local/bin/sari`)
- system install: 시스템 Python site-packages

### (EN)
- Global DB: `~/.local/share/sari/index.db` (single DB only)
- Global registry: `~/.local/share/sari/server.json`
- Logs: `~/.local/share/sari/logs`
- Workspace config: `<workspace>/.sari/config.json` or `<workspace>/sari.json` (`db_path` ignored)
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

### 6.3 (KR) 수집 실행 예제
전역 설정(`~/.config/sari/config.json`)에 다중 워크스페이스를 등록한 뒤 실행합니다.

```json
{
  "workspace_roots": [
    "/Users/<user>/Documents/StockBatch",
    "/Users/<user>/Documents/StockFront",
    "/Users/<user>/Documents/StockManager"
  ]
}
```

실행:
```bash
SARI_CONFIG=~/.config/sari/config.json sari --transport stdio
```

CLI로 등록 후 실행:
```bash
sari roots add /Users/<user>/Documents/StockBatch
sari roots add /Users/<user>/Documents/StockFront
sari roots add /Users/<user>/Documents/StockManager
sari roots list
SARI_CONFIG=~/.config/sari/config.json sari --transport stdio
```

### 6.3 (EN) Collection Example
Register multi-workspaces in the global config (`~/.config/sari/config.json`) and run.

```json
{
  "workspace_roots": [
    "/Users/<user>/Documents/StockBatch",
    "/Users/<user>/Documents/StockFront",
    "/Users/<user>/Documents/StockManager"
  ]
}
```

Run:
```bash
SARI_CONFIG=~/.config/sari/config.json sari --transport stdio
```

CLI registration then run:
```bash
sari roots add /Users/<user>/Documents/StockBatch
sari roots add /Users/<user>/Documents/StockFront
sari roots add /Users/<user>/Documents/StockManager
sari roots list
SARI_CONFIG=~/.config/sari/config.json sari --transport stdio
```

주의/Note:
- 상위/하위가 중첩되는 경로는 피하세요.
- Avoid nested roots (parent/child) to prevent duplicate scans.

---

## 7. 데이터 수집 정책 / Indexing Policy

### (KR)
Sari는 다음 순서로 수집 대상(workspace)을 결정합니다.

1. 전역/워크스페이스 설정의 `workspace_roots`
2. `SARI_WORKSPACE_ROOT` 환경변수
3. 클라이언트가 전달한 `rootUri/rootPath` (MCP 초기화 시)
4. 위가 없으면 **현재 실행 디렉토리(CWD)**

즉, **명시 설정이 없으면 CLI를 실행한 위치가 워크스페이스로 간주**되어 그 위치 기준으로 수집됩니다.  
레포 루트가 아니라 하위 폴더에서 실행하면 **일부만 인덱싱**될 수 있으니 주의하세요.

추가 팁:
- `.sariroot` 파일이 있으면 해당 위치를 프로젝트 루트로 고정합니다.
- 레포 최상단에서 실행하거나 `workspace_roots`를 명시하는 것을 권장합니다.

예시:
1) 전역 설정에 `workspace_roots`가 있고, 다른 디렉토리에서 CLI 실행
```json
// ~/.config/sari/config.json
{
  "workspace_roots": [
    "/Users/<user>/Documents/StockBatch",
    "/Users/<user>/Documents/StockFront"
  ]
}
```
```bash
cd /Users/<user>/Documents/OtherRepo
sari --transport stdio
```
결과: `StockBatch`, `StockFront`, **그리고 `OtherRepo`까지** 함께 수집됩니다.

2) Gemini/Codex 설정이 서로 다른 경우
```json
// Gemini: workspace_roots = A,B
// Codex:  workspace_roots = C
```
두 CLI를 동시에 켜면 전역 DB에 **A+B+C가 모두 섞여 인덱싱**됩니다.

해결:
- 두 CLI 모두 동일한 `SARI_CONFIG` 사용
- `workspace_roots` 통일
- 필요 시 `root_ids`/`repo`로 스코프 제한

### (EN)
Sari determines indexing roots in this order:

1. `workspace_roots` from global/workspace config
2. `SARI_WORKSPACE_ROOT` environment variable
3. `rootUri/rootPath` from MCP initialize
4. Fallback to **current working directory (CWD)**

If no explicit config is provided, **the CLI working directory is treated as the workspace**,  
so running from a subdirectory may index only a subset of the repo.

Tips:
- Add a `.sariroot` file to pin the project root.
- Prefer running from repo root or explicitly set `workspace_roots`.

Examples:
1) Global `workspace_roots` set, but CLI is executed from another directory
```json
// ~/.config/sari/config.json
{
  "workspace_roots": [
    "/Users/<user>/Documents/StockBatch",
    "/Users/<user>/Documents/StockFront"
  ]
}
```
```bash
cd /Users/<user>/Documents/OtherRepo
sari --transport stdio
```
Result: `StockBatch`, `StockFront`, **and `OtherRepo`** are indexed together.

2) Gemini/Codex configs differ
```json
// Gemini: workspace_roots = A,B
// Codex:  workspace_roots = C
```
Running both will **mix A+B+C into the same global DB**.

Fix:
- Use the same `SARI_CONFIG` in both CLIs
- Align `workspace_roots`
- Scope with `root_ids`/`repo` when needed

---

## 8. 설정 레퍼런스 / Configuration Reference

다음은 주요 설정값입니다.

| Key | Description (KR) | Description (EN) | Default |
| --- | --- | --- | --- |
| `workspace_root` | 기본 워크스페이스 루트 | Default workspace root | 현재 작업 디렉토리 |
| `workspace_roots` | 다중 워크스페이스 목록 | Multi-workspace roots | `[workspace_root]` |
| `db_path` | DB 파일 경로 (전역 설정만 적용) | DB file path (global only) | `~/.local/share/sari/index.db` |
| `include_ext` | 인덱싱 확장자 | File extensions to index | `.py, .js, .ts, .java, ...` |
| `include_files` | 확장자와 무관한 추가 파일 | Extra files regardless of extension | `[]` |
| `exclude_dirs` | 제외 디렉토리 | Excluded directories | `.git, node_modules, .venv, ...` |
| `exclude_globs` | 제외 패턴 | Excluded glob patterns | `.venv*, venv*, env*, *.egg-info` |
| `max_depth` | 탐색 최대 깊이 | Max directory depth | `20` |
| `scan_interval_seconds` | 자동 스캔 주기 | Auto-scan interval (sec) | `180` |
| `store_content` | 원문 저장 여부 | Store file content | `true` |
| `http_api_host` | HTTP 호스트 | HTTP host | `127.0.0.1` |
| `http_api_port` | HTTP 포트 | HTTP port | `47777` |
| `DAEMON_AUTOSTOP` | 마지막 연결 종료 시 즉시 종료 | Autostop on last session close | `true` |
| `DAEMON_IDLE_SEC` | idle 타임아웃(옵션) | Idle timeout seconds (optional) | `0` |

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

---

## 10. 부분응답 모드 / Partial-Response Mode

### (KR)
DB I/O 오류나 인덱싱 미완료 상태에서도 **부분 결과**를 반환합니다.  
응답 메타에 `partial=true`, `db_health`, `index_ready`가 표시됩니다.

### (EN)
Even when DB I/O fails or indexing is incomplete, Sari can return **partial results**.  
The response meta includes `partial=true`, `db_health`, and `index_ready`.
