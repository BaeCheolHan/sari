# Sari – 로컬 코드 검색/인덱싱 MCP 서버

Sari는 로컬에서 동작하는 코드 검색/인덱싱 MCP 서버입니다. 소스코드를 외부로 보내지 않고, 로컬에서 빠르게 검색하도록 설계되어 있습니다.

---

## 0. 소개

Sari는 다음을 제공합니다.
- 대규모 코드베이스 로컬 인덱싱/검색
- MCP stdio 기반 연동
- 다중 워크스페이스 수집
- 명확한 설정/데이터 경로 분리

---

## 1. 설치 / Installation

### 1.1 권장: uv tool 설치 (가장 추천)
Sari는 MCP 서버 도구이므로, 컴퓨터에 한 번만 설치하여 모든 프로젝트에서 공용으로 사용하는 것이 가장 효율적입니다. `uv tool`은 격리된 환경을 유지하면서도 실행 경로를 하나로 고정해줍니다.

*   **장점**: 중복 설치 방지, MCP 설정 경로(`command`) 고정, 어디서든 `sari` 명령어 사용 가능.

```bash
uv tool install sari
```

설치 후 다음 명령어로 **절대 경로**를 확인하여 MCP 설정에 사용하세요.
```bash
which sari
# 예시 결과: /Users/yourname/.local/bin/sari
```

### 1.2 선택 사항: venv 설치 (프로젝트별 격리)
특정 프로젝트에만 Sari를 종속시키고 싶을 때 사용합니다. 워크스페이스마다 별도로 설치해야 하므로 디스크 용량을 차지하며, MCP 설정의 `command` 경로를 매번 수정해야 하는 번거로움이 있습니다.

```bash
uv venv .venv
source .venv/bin/activate
uv pip install sari
```

---

## 2. MCP 클라이언트 설정

설치 방식에 따라 `command`와 `args` 설정이 달라집니다. 본인의 설치 방식에 맞는 설정을 사용하세요.

### 2.1 Gemini CLI 설정 (~/.gemini/settings.json)

**A. `uv tool install`로 설치한 경우 (권장)**
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

**B. `venv`에 설치한 경우**
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

### 2.2 Codex CLI 설정 (~/.codex/config.toml)

**A. `uv tool install`로 설치한 경우 (권장)**
```toml
[mcp_servers.sari]
command = "/Users/yourname/.local/bin/sari"
args = ["--transport", "stdio"]

[mcp_servers.sari.env]
SARI_CONFIG = "/abs/path/to/workspace/.sari/config.json"
```

**B. `venv`에 설치한 경우**
```toml
[mcp_servers.sari]
command = "/abs/path/to/project/.venv/bin/python"
args = ["-m", "sari", "--transport", "stdio"]

[mcp_servers.sari.env]
SARI_CONFIG = "/abs/path/to/workspace/.sari/config.json"
```

> **참고**: TOML 설정 시 `env` 항목은 별도의 테이블(`[mcp_servers.sari.env]`)로 분리하거나 인라인 테이블 형식으로 작성할 수 있습니다. 위 예시는 가독성이 좋은 분리형 방식을 사용했습니다.

> **Tip**: `command` 경로는 반드시 본인의 시스템에서 `which sari` 또는 `which python`으로 확인한 **절대 경로**를 입력해야 합니다.

---

## 3. 실행 모드 / Runtime Modes

### 3.1 stdio (MCP 기본값)
stdio 모드는 데몬(Daemon) 프로세스를 통해 빠르게 동작합니다.

데몬 시작 (최초 1회 또는 재부팅 후):
```bash
sari daemon start -d
```

### 3.2 HTTP API
브라우저나 다른 도구에서 API로 접근하고 싶을 때 사용합니다.
```bash
sari --transport http --http-api-port 47777
```

- 전역 DB: `~/.local/share/sari/index.db`
- 전역 레지스트리: `~/.local/share/sari/server.json`
- 로그: `~/.local/share/sari/logs`
- 워크스페이스 설정: `<workspace>/.sari/config.json` 또는 `<workspace>/sari.json`
- 전역 설정: `~/.config/sari/config.json`

---

## 4. 다중 워크스페이스

### 4.1 CLI
```bash
sari roots add /path/to/workspaceA
sari roots add /path/to/workspaceB
sari roots list
```

### 4.2 설정 파일
```json
{
  "workspace_roots": [
    "/path/to/workspaceA",
    "/path/to/workspaceB"
  ]
}
```

주의:
- 상위/하위가 중첩되는 경로는 피하세요.

---

## 5. 설정 예시

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

---

## 6. 업데이트

```bash
source .venv/bin/activate
uv pip install -U sari
```

---

## 7. 트러블슈팅

문제가 발생하면 `TROUBLESHOOTING.md`를 확인하세요.
