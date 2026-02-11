# Sari - 로컬 MCP 코드 검색 + 데몬 + 대시보드

[English](README.md)

Sari는 대규모 코드베이스를 위한 로컬 우선 MCP 서버입니다.

주요 기능:

- 빠른 로컬 인덱싱/검색
- 다중 워크스페이스 등록
- 데몬 라이프사이클 관리
- 웹 대시보드 제공

소스코드는 외부로 업로드되지 않고 로컬에서 처리됩니다.

## 빠른 시작

### 1. 설치

권장 방식:

```bash
uv tool install sari
```

설치 경로 확인:

```bash
which sari
```

### 2. 데몬 시작

```bash
sari daemon start -d
```

### 3. 수집할 워크스페이스 등록

```bash
sari roots add /absolute/path/to/repo-a
sari roots add /absolute/path/to/repo-b
sari roots list
```

### 4. 대시보드 접속

기본 URL:

```text
http://127.0.0.1:47777/
```

대시보드에서 상태, 헬스, 워크스페이스 목록, 워크스페이스별 인덱싱 파일 수를 확인할 수 있습니다.

### 5. 동작 확인

```bash
sari daemon status
sari status
```

## MCP 클라이언트 설정

최소 설정(권장):

### Codex CLI (`~/.codex/config.toml`)

```toml
[mcp_servers.sari]
command = "sari"
args = ["--transport", "stdio", "--format", "pack"]
startup_timeout_sec = 60
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

호스트별 설정 스니펫 출력:

```bash
sari --cmd install --host codex --print
sari --cmd install --host gemini --print
```

## 워크스페이스 등록 모델

Sari는 CLI별 워크스페이스 하드코딩이 아니라 config roots 기반으로 동작합니다.

핵심 명령어:

```bash
sari roots add /abs/path
sari roots remove /abs/path
sari roots list
```

설정 파일 해석 순서:

1. `<workspace>/.sari/mcp-config.json` 존재 시 우선 사용
2. 없으면 `~/.config/sari/config.json` 사용

## 데몬 라이프사이클 (현재 정책)

- 동일 endpoint(host/port) 기준 단일 데몬 정책
- 버전 불일치 시 같은 endpoint에서 교체
- 자동종료는 세션 기반:
  - 활성 클라이언트 세션이 1개 이상이면 데몬 유지
  - 활성 세션이 0이 되면 grace 이후 자동 종료
- 고아 인덱싱 워커 보호:
  - 부모 데몬 사망 시 워커 자폭
  - `sari daemon stop` 시 고아 워커 수거

자주 쓰는 명령:

```bash
sari daemon start -d
sari daemon ensure
sari daemon status
sari daemon stop
sari daemon refresh
```

## 대시보드 및 HTTP 엔드포인트

데몬 HTTP가 활성화되면 다음 엔드포인트를 사용할 수 있습니다.

- `/` : 대시보드 UI
- `/health` : liveness
- `/status` : 런타임/인덱싱/시스템 요약
- `/workspaces` : 등록 워크스페이스 상태
- `/search?q=...&limit=...` : HTTP 검색
- `/rescan` : 리스캔 트리거
- `/health-report` : 확장 진단

예시:

```bash
curl "http://127.0.0.1:47777/status"
curl "http://127.0.0.1:47777/workspaces"
```

## 자주 하는 작업

### 강제 리스캔

```bash
sari --cmd index
```

### 진단 실행

```bash
sari doctor
```

### 현재 설정 확인

```bash
sari --cmd config show
```

## 데이터 경로

- DB: `~/.local/share/sari/index.db`
- Registry: `~/.local/share/sari/server.json`
- Logs: `~/.local/share/sari/logs/`
- 전역 config: `~/.config/sari/config.json`
- 워크스페이스 config: `<workspace>/.sari/mcp-config.json`

## 트러블슈팅

### 대시보드에 CJK tokenizer dictionary 오류가 보일 때

선택 의존성 설치:

```bash
uv pip install lindera-python-ipadic
```

### 포트 충돌

```bash
sari daemon stop
sari daemon start -d --daemon-port 47789
```

### CLI 업데이트 후 구버전 데몬이 남아있을 때

```bash
sari daemon refresh
```

## 업데이트 / 제거

```bash
uv tool upgrade sari
uv tool uninstall sari
```
