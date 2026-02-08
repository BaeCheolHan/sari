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

## 1. 설치 (고정)

Sari는 **venv 환경에서만 설치/실행**합니다.

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

---

## 2. stdio (MCP 고정 운용)

**stdio는 데몬 프록시로 동작합니다.**  
즉, stdio를 사용하려면 데몬이 필요합니다.

### 2.1 데몬 시작
```bash
sari daemon start -d
```

### 2.2 실행 (stdio)
```bash
python -m sari --transport stdio
```

### 2.3 Gemini CLI 설정 (stdio)
`~/.gemini/settings.json`
```json
{
  "mcpServers": {
    "sari": {
      "command": "/abs/path/to/.venv/bin/python",
      "args": ["-m", "sari", "--transport", "stdio"],
      "env": {
        "SARI_CONFIG": "/abs/path/to/project/.sari/config.json"
      }
    }
  }
}
```

### 2.4 Codex CLI 설정 (stdio)
`~/.codex/config.toml`
```toml
[mcp_servers.sari]
command = "/abs/path/to/.venv/bin/python"
args = ["-m", "sari", "--transport", "stdio"]
env = { SARI_CONFIG = "/abs/path/to/project/.sari/config.json" }
```

---

## 3. 데이터/로그 경로

- 전역 DB: `~/.local/share/sari/index.db`
- 전역 레지스트리: `~/.local/share/sari/server.json`
- 로그: `~/.local/share/sari/logs`
- 워크스페이스 설정: `<workspace>/.sari/config.json` 또는 `<workspace>/sari.json`
- 전역 설정: `~/.config/sari/config.json`

---

## 4. 다중 워크스페이스

### 5.1 CLI
```bash
sari roots add /path/to/workspaceA
sari roots add /path/to/workspaceB
sari roots list
```

### 5.2 설정 파일
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
