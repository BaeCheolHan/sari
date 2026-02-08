# Sari – Local Code Search & Indexing Agent (MCP)

Sari is a high‑performance local code search and indexing agent that implements the Model Context Protocol (MCP). It runs entirely on your machine and helps AI clients search large codebases without sending your source to external servers.

Sari focuses on:
- Fast, local indexing of large repositories
- Safe MCP integration for AI assistants (stdio/HTTP)
- Clear separation of workspace configs and global data
- Multi‑workspace awareness without duplicating index state

---

## English

### 1. Installation

Sari follows a policy of **plain install without extras**. The default package already includes the tokenizer and tree‑sitter dependencies required by Sari.

#### Option A: uv (recommended)
```bash
# Create venv once
uv venv .venv

# Install into the venv
uv pip install sari
```

If you want to install into the system Python (not recommended):
```bash
uv pip install --system sari
```

#### Option B: pip
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

- `uv pip install sari` → installed into the uv-managed environment
- `pip install sari` → installed into the current Python environment

To locate the package:
```bash
python -c "import sari,inspect; print(inspect.getfile(sari))"
```

### 3. Database & Storage Locations

Sari writes its local database and runtime files to the following locations:

- **Global DB (default)**: `~/.local/share/sari/index.db`
- **Workspace-local DB**: `<workspace>/.sari/index.db` (if the `.sari` directory exists)
- **Registry file**: `~/.local/share/sari/server.json`
- **Logs**: `~/.local/share/sari/logs` (default; can be overridden)
- **Config (workspace)**: `<workspace>/.sari/config.json` or `<workspace>/sari.json`
- **Config (global)**: `~/.config/sari/config.json`

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

Sari can index multiple workspace roots in one configuration.

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
- Avoid overlapping roots (e.g., `/repo` and `/repo/sub`), otherwise indexing can be duplicated.
- The MCP server will use the first root as the primary workspace for sessions.

### 7. Configuration Reference (Common)

Configuration file keys:

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

Environment variables (high‑impact):
- `SARI_WORKSPACE_ROOT`
- `SARI_CONFIG`
- `SARI_LOG_DIR`
- `SARI_DAEMON_PORT`
- `SARI_HTTP_API_PORT`
- `SARI_ENGINE_INDEX_POLICY` (global | roots_hash | per_root)

Full list: `src/sari/docs/reference/ENVIRONMENT.md`

### 8. Client & IDE Integration

Sari supports **both stdio and HTTP** transports.

#### 8.0 Transport Modes

**stdio (recommended for MCP clients)**
```bash
sari --transport stdio --format pack
```

**HTTP (explicit server mode)**
```bash
sari --transport http --http-api --http-daemon
```

HTTP endpoint:
```
http://127.0.0.1:47777/mcp
```

#### 8.1 Gemini CLI (`.gemini/settings.json`) – stdio
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

#### 8.2 Codex CLI (`.codex/config.toml`) – stdio
```toml
[mcp_servers.sari]
command = "sari"
args = ["--transport", "stdio", "--format", "pack"]
env = { SARI_CONFIG = "/absolute/path/to/your/project/.sari/config.json" }
startup_timeout_sec = 60
```

#### 8.3 Claude Desktop (macOS) – stdio
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

#### 8.4 Cursor – stdio
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

#### 8.5 VS Code (MCP-compatible plugins) – stdio
Use the same MCP JSON format as Cursor or Claude.

#### 8.6 IntelliJ / JetBrains – stdio
If the MCP plugin supports JSON config, use the same format as Cursor.

#### Optional: Auto‑write configs (stdio)
```bash
sari --cmd install --host codex
sari --cmd install --host gemini
sari --cmd install --host claude
sari --cmd install --host cursor
```

### 9. Updating Sari

```bash
# uv
uv pip install -U sari

# pip
pip install -U sari
```

If installed from source:
```bash
git pull
pip install -e .
```

---

## 한국어

### 1. 설치 방법

Sari는 로컬에서 대규모 코드베이스를 빠르게 색인하고, MCP를 통해 AI 도구에 안전하게 연결하는 로컬 검색 에이전트입니다.

Sari의 핵심 지향점:
- 대규모 저장소의 빠른 로컬 인덱싱
- stdio/HTTP MCP 연동 지원
- 전역 데이터와 워크스페이스 설정 분리
- 다중 워크스페이스 인덱싱의 중복 방지

Sari는 **기본 설치만** 사용합니다. 기본 패키지에 토크나이저와 tree‑sitter 계열 의존성이 포함됩니다.

#### 방법 A: uv (권장)
```bash
# 최초 1회 가상환경 생성
uv venv .venv

# venv에 설치
uv pip install sari
```

시스템 Python에 설치하려면(비권장):
```bash
uv pip install --system sari
```

#### 방법 B: pip
```bash
pip install sari
```

#### 소스 설치 (개발용)
```bash
git clone https://github.com/BaeCheolHan/sari.git
cd sari
pip install -e .
```

### 2. 설치 위치

Sari는 **현재 활성 Python 환경**에 설치됩니다.

- `uv pip install sari` → uv 환경
- `pip install sari` → 현재 Python 환경

설치 경로 확인:
```bash
python -c "import sari,inspect; print(inspect.getfile(sari))"
```

### 3. DB 및 파일 저장 위치

- **전역 DB (기본)**: `~/.local/share/sari/index.db`
- **워크스페이스 로컬 DB**: `<workspace>/.sari/index.db` (`.sari` 디렉터리가 있으면 로컬 사용)
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
- 중첩 워크스페이스는 중복 인덱싱을 유발할 수 있습니다.

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
- `SARI_ENGINE_INDEX_POLICY`

전체 목록: `src/sari/docs/reference/ENVIRONMENT.md`

### 8. CLI/IDE 연동 가이드

Sari는 **stdio / HTTP** 모두 지원합니다.

#### 8.0 전송 모드

**stdio (MCP 클라이언트 권장)**
```bash
sari --transport stdio --format pack
```

**HTTP (서버 모드)**
```bash
sari --transport http --http-api --http-daemon
```

HTTP 엔드포인트:
```
http://127.0.0.1:47777/mcp
```

#### 8.1 Gemini CLI – stdio
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

#### 8.2 Codex CLI – stdio
```toml
[mcp_servers.sari]
command = "sari"
args = ["--transport", "stdio", "--format", "pack"]
env = { SARI_CONFIG = "/absolute/path/to/your/project/.sari/config.json" }
startup_timeout_sec = 60
```

#### 8.3 Claude Desktop – stdio
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

#### 8.4 Cursor – stdio
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

#### 8.5 VS Code / IntelliJ – stdio
Cursor와 동일한 MCP JSON 설정을 사용하면 됩니다.

#### 자동 설정 쓰기 (stdio)
```bash
sari --cmd install --host codex
sari --cmd install --host gemini
sari --cmd install --host claude
sari --cmd install --host cursor
```

### 9. 업데이트 방법

```bash
# uv
uv pip install -U sari

# pip
pip install -U sari
```

소스 설치 시:
```bash
git pull
pip install -e .
```
