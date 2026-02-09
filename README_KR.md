# Sari – 로컬 코드 검색/인덱싱 MCP 서버

[English](README.md)

Sari는 로컬에서 동작하는 고성능 코드 검색 및 인덱싱 MCP 서버입니다. 소스코드를 외부 서버로 전송하지 않고 로컬에서 모든 작업을 수행하며, 대규모 코드베이스에서도 빠른 검색과 심볼 분석, 호출 그래프(Call Graph) 기능을 제공합니다.

---

## 1. 주요 특징
- **로컬 우선**: 소스코드를 로컬에서 인덱싱하여 보안과 속도를 동시에 확보합니다.
- **고성능 검색**: `tantivy`(Rust 기반 엔진)를 사용하여 수만 개의 파일도 빠르게 검색합니다.
- **다중 워크스페이스**: 여러 프로젝트를 동시에 등록하고 통합 검색할 수 있습니다.
- **구조 분석**: `tree-sitter`를 통해 함수, 클래스 간의 관계를 분석하고 호출 그래프를 생성합니다.

---

## 2. 설치 방법

### 2.1 권장: uv tool 설치 (가장 추천)
Sari는 여러 프로젝트에서 공용으로 사용하는 도구이므로, `uv tool`을 통한 전역 설치를 강력히 권장합니다. 격리된 환경을 유지하면서도 실행 경로를 하나로 고정하여 관리가 매우 편리합니다.

*   **장점**: 중복 설치 방지, MCP 설정 경로(`command`) 고정, 어디서든 `sari` 명령어 사용 가능.

```bash
uv tool install sari
```

설치 후 다음 명령어로 **절대 경로**를 확인하여 MCP 설정에 사용하세요.
```bash
which sari
# 예시 결과: /Users/yourname/.local/bin/sari
```

### 2.2 선택 사항: venv 설치 (프로젝트별 격리)
특정 프로젝트에만 Sari를 설치하고 싶을 때 사용합니다. 워크스페이스마다 별도로 설치해야 하므로 디스크 용량을 차지하며, MCP 설정의 `command` 경로를 프로젝트마다 수정해야 합니다.

```bash
uv venv .venv
source .venv/bin/activate
uv pip install sari
```

---

## 3. 실행 모드

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

---

## 4. MCP 클라이언트 설정

설치 방식에 따라 `command`와 `args` 설정이 달라집니다. 본인의 설치 방식에 맞는 설정을 사용하세요.

### 4.1 Gemini CLI 설정 (~/.gemini/settings.json)

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

---

### 4.2 Codex CLI 설정 (~/.codex/config.toml)

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

---

## 5. 데이터 및 설정 경로

- **전역 DB**: `~/.local/share/sari/index.db`
- **전역 레지스트리**: `~/.local/share/sari/server.json`
- **로그**: `~/.local/share/sari/logs`
- **워크스페이스 설정**: `<workspace>/.sari/config.json`
- **전역 설정**: `~/.config/sari/config.json`

---

## 6. 다중 워크스페이스 관리

### 6.1 CLI로 추가
```bash
sari roots add /path/to/workspaceA
sari roots add /path/to/workspaceB
sari roots list
```

### 6.2 설정 파일로 관리
```json
{
  "workspace_roots": [
    "/path/to/workspaceA",
    "/path/to/workspaceB"
  ]
}
```

---

## 7. 설정 레퍼런스

| 설정 키 | 설명 | 기본값 |
| --- | --- | --- |
| `workspace_roots` | 다중 워크스페이스 목록 | `[CWD]` |
| `include_ext` | 인덱싱할 파일 확장자 | `.py, .js, .ts, .java, ...` |
| `exclude_dirs` | 제외할 디렉토리 | `.git, node_modules, .venv, ...` |
| `max_depth` | 디렉토리 탐색 최대 깊이 | `20` |
| `scan_interval_seconds` | 자동 스캔 주기 (초) | `180` |

---

## 8. 트러블슈팅
문제가 발생하면 `docs/TROUBLESHOOTING.md`를 확인하세요.