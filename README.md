# sari v2

LSP-first 로컬 인덱싱/검색 엔진 + MCP 데몬.

## 설치

```bash
uv tool install sari
# 또는
python3 -m pip install sari
```

## 기본 사용

```bash
sari doctor
sari daemon start
sari roots add /absolute/path/to/repo
sari roots deactivate /absolute/path/to/repo
sari roots activate /absolute/path/to/repo
```

## Workspace 활성 정책 (Soft-OFF)

`is_active`는 수집/도구 접근 제어 플래그다.

- `is_active=true`: 수집 루프와 MCP/HTTP repo 해석 경로에서 정상 접근된다.
- `is_active=false`: 수집 스케줄 및 watcher 등록에서 제외되고, 도구 접근은 `ERR_WORKSPACE_INACTIVE`로 차단된다.
- Soft-OFF 정책: 비활성화 시 기존 인덱스/메타데이터는 즉시 삭제하지 않는다(데이터 유지).

자세한 운영 규칙은 `docs/workspace_activation_policy.md`를 참고한다.

## MCP 연동 (권장)

```bash
sari install --host gemini
sari install --host codex
```

- Gemini/Codex 설정에 `command = "sari"` + `args = ["mcp","stdio"]`를 자동 반영한다.
- Codex 설정에는 `startup_timeout_sec = 45`를 기본 반영한다.
- 기존 설정 파일은 `.bak.<timestamp>`로 백업된다.

### MCP handshake timeout 대응

MCP 클라이언트에서 아래와 같은 메시지가 보이면 startup timeout이 짧은 경우가 많다.

- `MCP client for "sari" timed out after 10 seconds`
- `MCP startup incomplete`

Codex 설정(`~/.codex/config.toml`)에서 `startup_timeout_sec`를 늘려준다.

```toml
[mcp_servers.sari]
command = "sari"
args = ["mcp", "stdio"]
startup_timeout_sec = 45
```

- 권장 시작값: `30`
- 대형 DB/느린 디스크/초기 마이그레이션 환경: `45~60`

## 수동 설정 예시

### Gemini (`~/.gemini/settings.json`)

```json
{
  "mcpServers": {
    "sari": {
      "command": "sari",
      "args": ["mcp", "stdio"]
    }
  }
}
```

### Codex (`~/.codex/config.toml`)

```toml
[mcp_servers.sari]
command = "sari"
args = ["mcp", "stdio"]
startup_timeout_sec = 45
```

## Troubleshooting

### `sqlite3.OperationalError: no such column: repo_id`

기존(구버전) `state.db`를 현재 바이너리와 함께 사용할 때 발생할 수 있다.

복구 절차:

1. 기존 DB 백업
2. 새 DB 경로로 부팅해 초기 스키마/마이그레이션을 완료
3. `sari doctor`와 MCP 연결 재확인

예시:

```bash
# 1) 백업
cp ~/.local/share/sari-v2/state.db ~/.local/share/sari-v2/state.db.bak.$(date +%Y%m%d-%H%M%S)

# 2) 새 DB로 실행(임시/영구 경로 모두 가능)
export SARI_DB_PATH=~/.local/share/sari-v2/state.new.db

# 3) 상태 확인
sari doctor
```

### 설치 직후 최소 점검 순서

```bash
sari doctor
sari install --host codex
# Codex config.toml에서 startup_timeout_sec = 30~60 확인
```

## 개발 검증

```bash
pytest -q
tools/ci/run_release_gate.sh
tools/manual/test_mcp_call_flow.sh /absolute/path/to/repo
```

## GitHub Actions 배포

Release 워크플로우 파일: `.github/workflows/release-pypi.yml`

### 1) TestPyPI 선검증 (권장)

1. GitHub Actions에서 `Release PyPI`를 수동 실행한다.
2. 입력값 `publish_to_testpypi=true`로 실행한다.
3. `build` job에서 release gate + wheel/sdist 빌드 + twine check 통과를 확인한다.
4. `publish-testpypi` job 성공과 `release-dist` artifact 업로드를 확인한다.

### 2) PyPI 실배포

1. `pyproject.toml` 버전을 확정한다.
2. 동일 버전 태그(`v<version>`)를 push 한다. 예: `v2.0.14`
3. 워크플로우의 tag/version 일치 검증 통과 후 `publish-pypi` job 성공을 확인한다.

## 로컬 wheel 테스트 (글로벌 tool 오염 방지)

로컬 빌드 산출물 검증은 `uv tool install dist/*.whl` 대신 아래 스크립트를 사용한다.

```bash
python3 -m build
tools/manual/test_local_wheel_ephemeral.sh
```

- 위 방식은 `uvx --from <wheel>`로 일회성 실행만 하므로, `~/.local/bin/sari` 글로벌 설치를 덮어쓰지 않는다.
- 글로벌 업그레이드는 계속 `uv tool upgrade sari`를 사용한다.

### 실수로 글로벌 tool을 로컬 wheel로 덮어쓴 경우 복구

```bash
tools/manual/repair_global_sari_tool.sh
# 특정 버전으로 복구
tools/manual/repair_global_sari_tool.sh 2.0.13
```
