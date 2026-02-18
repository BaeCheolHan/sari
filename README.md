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
```
