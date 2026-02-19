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
```

## MCP 연동 (권장)

```bash
sari install --host gemini
sari install --host codex
```

- Gemini/Codex 설정에 `command = "sari"` + `args = ["mcp","stdio"]`를 자동 반영한다.
- 기존 설정 파일은 `.bak.<timestamp>`로 백업된다.

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
```

## 개발 검증

```bash
pytest -q
tools/ci/run_release_gate.sh
tools/manual/test_mcp_call_flow.sh /absolute/path/to/repo
```

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
