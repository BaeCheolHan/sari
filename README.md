# Sari – Local Code Search & Indexing Agent (MCP)

Sari is a high‑performance local code search and indexing agent that implements the Model Context Protocol (MCP). It runs entirely on your machine and helps AI clients search large codebases without sending your source to external servers.

Sari focuses on:
- Fast, local indexing of large repositories
- Safe MCP integration for AI assistants (stdio/HTTP)
- Clear separation of workspace configs and global data
- Multi‑workspace awareness without duplicating index state

---

## English

### 0. Overview

Sari provides:
- Local indexing + search for large codebases
- MCP server for stdio and HTTP transports
- Multi‑workspace collection with a single registry

### 1. Installation (Recommended)

Sari is installed into the **active Python environment**. If you are using `uv`, you must create a venv first.

#### Option A: `uv` + venv (recommended)
```bash
# 1) Create venv once
uv venv .venv

# 2) Install Sari into the venv
uv pip install sari

# 3) (Optional) Upgrade later
uv pip install -U sari
```

#### Option B: `uv` system install (not recommended)
```bash
uv pip install --system sari
```

#### Option C: `pip`
```bash
pip install sari
```

#### From source (development)
```bash
git clone https://github.com/BaeCheolHan/sari.git
cd sari
pip install -e .
```

### 2. Where Sari Is Installed

Sari is installed into **the active Python environment**.

Check install path:
```bash
python -c "import sari,inspect; print(inspect.getfile(sari))"
```

Check version:
```bash
python -c "import sari; print(sari.__version__)"
```

### 3. Database & Storage Locations

Sari writes runtime state here:

- **Global DB (default)**: `~/.local/share/sari/index.db`
- **Workspace-local DB**: `<workspace>/.sari/index.db` (if `.sari` exists)
- **Registry**: `~/.local/share/sari/server.json`
- **Logs**: `~/.local/share/sari/logs`
- **Workspace config**: `<workspace>/.sari/config.json` or `<workspace>/sari.json`
- **Global config**: `~/.config/sari/config.json`

### 4. Quick Start

```bash
# Start daemon in background
sari daemon start -d

# Check status
sari daemon status

# Run MCP proxy (stdio ↔ daemon)
sari proxy
```

### 5. CLI Commands (Core)

```bash
sari daemon start -d
sari daemon stop
sari daemon status
sari daemon ensure

sari proxy
sari status
sari doctor
sari index

sari config show
sari roots list
sari roots add /absolute/path/to/workspace
sari roots remove /absolute/path/to/workspace
```

### 6. Multi‑Workspace Collection

#### Option A: CLI
```bash
sari roots add /path/to/workspaceA
sari roots add /path/to/workspaceB
sari roots list
```

#### Option B: Config file (`.sari/config.json`)
```json
{
  "roots": [
    "/path/to/workspaceA",
    "/path/to/workspaceB"
  ]
}
```

Notes:
- Avoid overlapping roots (e.g., `/repo` and `/repo/sub`) to prevent duplication.
- The MCP server uses the first root as the primary workspace for the session.

### 7. Configuration Reference (Common)

```json
{
  "workspace_root": "/path/to/workspace",
  "workspace_roots": ["/path/to/workspaceA", "/path/to/workspaceB"],
  "db_path": "/custom/path/index.db",
  "include_ext": [".py", ".js", ".ts", ".java", ".rs"],
  "include_files": ["Dockerfile", "Makefile"],
  "exclude_dirs": [".git", "node_modules", ".sari"],
  "exclude_globs": ["**/dist/**"],
  "max_depth": 20,
  "scan_interval_seconds": 180,
  "store_content": true
}
```

High‑impact environment variables:
- `SARI_WORKSPACE_ROOT`
- `SARI_CONFIG`
- `SARI_LOG_DIR`
- `SARI_DAEMON_PORT`
- `SARI_HTTP_API_PORT`
- `SARI_ENGINE_INDEX_POLICY` (global | roots_hash | per_root)

Full list: `src/sari/docs/reference/ENVIRONMENT.md`

### 8. Transport & Client Integration (stdio / HTTP)

Sari supports **both stdio and HTTP**.

#### 8.1 stdio (recommended for MCP clients)
```bash
sari --transport stdio --format pack
```

#### 8.2 HTTP server mode
```bash
sari --transport http --http-api --http-daemon
```

HTTP endpoint:
```
http://127.0.0.1:47777/mcp
```

#### 8.3 Gemini CLI (`.gemini/settings.json`) – stdio
```json
{
  "mcpServers": {
    "sari": {
      "command": "sari",
      "args": ["--transport", "stdio", "--format", "pack"],
      "env": {
        "SARI_CONFIG": "/absolute/path/to/your/project/.sari/config.json"
      }
    }
  }
}
```

#### 8.4 Codex CLI (`.codex/config.toml`) – stdio
```toml
[mcp_servers.sari]
command = "sari"
args = ["--transport", "stdio", "--format", "pack"]
env = { SARI_CONFIG = "/absolute/path/to/your/project/.sari/config.json" }
startup_timeout_sec = 60
```

#### 8.5 Claude Desktop – stdio
```json
{
  "mcpServers": {
    "sari": {
      "command": "sari",
      "args": ["--transport", "stdio", "--format", "json"],
      "env": {
        "SARI_CONFIG": "/absolute/path/to/your/project/.sari/config.json"
      }
    }
  }
}
```

#### 8.6 Cursor / VS Code / IntelliJ – stdio
Use the same MCP JSON format as above.

#### Optional: auto‑write configs
```bash
sari --cmd install --host codex
sari --cmd install --host gemini
sari --cmd install --host claude
sari --cmd install --host cursor
```

### 9. Updating Sari

#### If installed via `uv` (venv)
```bash
uv pip install -U sari
```

#### If installed via `uv` (system)
```bash
uv pip install --system -U sari
```

#### If installed via `pip`
```bash
pip install -U sari
```

#### If installed from source
```bash
git pull
pip install -e .
```

### 10. Testing / Dev (tree‑sitter parsers)

If you run tests locally, you must install **language‑specific tree‑sitter packages** and pytest async plugin. For example:

```bash
pip install pytest pytest-asyncio
pip install tree-sitter-java tree-sitter-javascript tree-sitter-typescript tree-sitter-python
```

---

## 한국어

### 0. 소개

Sari는 다음을 제공합니다:
- 대규모 코드베이스 로컬 인덱싱 + 검색
- stdio / HTTP MCP 서버
- 다중 워크스페이스 수집을 위한 전역 레지스트리

### 1. 설치 방법 (권장)

Sari는 **현재 활성 Python 환경**에 설치됩니다. `uv`를 사용할 경우 **먼저 venv 생성이 필수**입니다.

#### 방법 A: `uv` + venv (권장)
```bash
# 1) venv 생성 (최초 1회)
uv venv .venv

# 2) venv에 설치
uv pip install sari

# 3) (선택) 업데이트
uv pip install -U sari
```

#### 방법 B: `uv` 시스템 설치 (비권장)
```bash
uv pip install --system sari
```

#### 방법 C: `pip`
```bash
pip install sari
```

#### 소스 설치 (개발용)
```bash
git clone https://github.com/BaeCheolHan/sari.git
cd sari
pip install -e .
```

### 2. 설치 위치 확인

Sari는 **활성 Python 환경**에 설치됩니다.

설치 경로 확인:
```bash
python -c "import sari,inspect; print(inspect.getfile(sari))"
```

버전 확인:
```bash
python -c "import sari; print(sari.__version__)"
```

### 3. DB 및 파일 저장 위치

- **전역 DB (기본)**: `~/.local/share/sari/index.db`
- **워크스페이스 로컬 DB**: `<workspace>/.sari/index.db` (`.sari` 폴더가 있으면 로컬 사용)
- **레지스트리**: `~/.local/share/sari/server.json`
- **로그**: `~/.local/share/sari/logs`
- **워크스페이스 설정**: `<workspace>/.sari/config.json` 또는 `<workspace>/sari.json`
- **전역 설정**: `~/.config/sari/config.json`

### 4. 빠른 시작

```bash
sari daemon start -d
sari daemon status
sari proxy
```

### 5. 주요 CLI

```bash
sari daemon start -d
sari daemon stop
sari daemon status
sari daemon ensure

sari proxy
sari status
sari doctor
sari index

sari config show
sari roots list
sari roots add /absolute/path/to/workspace
sari roots remove /absolute/path/to/workspace
```

### 6. 다중 워크스페이스 설정

#### 방법 A: CLI
```bash
sari roots add /path/to/workspaceA
sari roots add /path/to/workspaceB
sari roots list
```

#### 방법 B: 설정 파일
```json
{
  "roots": [
    "/path/to/workspaceA",
    "/path/to/workspaceB"
  ]
}
```

주의:
- 중첩 워크스페이스는 인덱싱 중복을 유발합니다.
- 세션의 기본 워크스페이스는 첫 번째 root 기준으로 잡힙니다.

### 7. 설정 값 안내 (대표)

```json
{
  "workspace_root": "/path/to/workspace",
  "workspace_roots": ["/path/to/workspaceA", "/path/to/workspaceB"],
  "db_path": "/custom/path/index.db",
  "include_ext": [".py", ".js", ".ts", ".java", ".rs"],
  "include_files": ["Dockerfile", "Makefile"],
  "exclude_dirs": [".git", "node_modules", ".sari"],
  "exclude_globs": ["**/dist/**"],
  "max_depth": 20,
  "scan_interval_seconds": 180,
  "store_content": true
}
```

환경변수(핵심):
- `SARI_WORKSPACE_ROOT`
- `SARI_CONFIG`
- `SARI_LOG_DIR`
- `SARI_DAEMON_PORT`
- `SARI_HTTP_API_PORT`
- `SARI_ENGINE_INDEX_POLICY` (global | roots_hash | per_root)

전체 목록: `src/sari/docs/reference/ENVIRONMENT.md`

### 8. stdio / HTTP 설정법

#### 8.1 stdio (MCP 클라이언트 권장)
```bash
sari --transport stdio --format pack
```

#### 8.2 HTTP 서버 모드
```bash
sari --transport http --http-api --http-daemon
```

HTTP 엔드포인트:
```
http://127.0.0.1:47777/mcp
```

#### 8.3 Gemini CLI – stdio
```json
{
  "mcpServers": {
    "sari": {
      "command": "sari",
      "args": ["--transport", "stdio", "--format", "pack"],
      "env": {
        "SARI_CONFIG": "/absolute/path/to/your/project/.sari/config.json"
      }
    }
  }
}
```

#### 8.4 Codex CLI – stdio
```toml
[mcp_servers.sari]
command = "sari"
args = ["--transport", "stdio", "--format", "pack"]
env = { SARI_CONFIG = "/absolute/path/to/your/project/.sari/config.json" }
startup_timeout_sec = 60
```

#### 8.5 Claude Desktop – stdio
```json
{
  "mcpServers": {
    "sari": {
      "command": "sari",
      "args": ["--transport", "stdio", "--format", "json"],
      "env": {
        "SARI_CONFIG": "/absolute/path/to/your/project/.sari/config.json"
      }
    }
  }
}
```

#### 8.6 Cursor / VS Code / IntelliJ – stdio
위 JSON 형식을 그대로 사용하면 됩니다.

#### 자동 설정 쓰기
```bash
sari --cmd install --host codex
sari --cmd install --host gemini
sari --cmd install --host claude
sari --cmd install --host cursor
```

### 9. 업데이트 방법

#### `uv` + venv 사용 시
```bash
uv pip install -U sari
```

#### `uv` 시스템 설치 시
```bash
uv pip install --system -U sari
```

#### `pip` 사용 시
```bash
pip install -U sari
```

#### 소스 설치 시
```bash
git pull
pip install -e .
```

### 10. 테스트 / 개발용 (tree‑sitter 파서)

테스트를 돌릴 경우 **언어별 tree‑sitter 패키지**와 pytest async 플러그인을 설치해야 합니다.

```bash
pip install pytest pytest-asyncio
pip install tree-sitter-java tree-sitter-javascript tree-sitter-typescript tree-sitter-python
```
