# Sari: The Ultra-Turbo Search Engine 🚀

Sari is a high-performance local code search and indexing agent, now modernized with an **"Ultra-Turbo"** architecture. It supports the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) for seamless integration with AI agents.

## ⚡ Why Sari is Faster Now?
- **Parallel Parsing**: Bypasses Python's GIL using `ProcessPoolExecutor`.
- **RAM-Backed Staging**: reaching hardware limits of RAM speed.
- **30GB MMAP I/O**: Near-zero search latency.
- **Intelligent Governor**: Automated speed scaling (0.3x ~ 2.5x).

## 🛠 Integration Guide

### 1. Gemini CLI (`.gemini/settings.json`)
Simplify your config. No more complex environment variables.
```json
{
  "mcpServers": {
    "sari": {
      "command": "python3",
      "args": ["-m", "sari.mcp.cli", "proxy", "--daemon-port", "47800"]
    }
  }
}
```

### 2. Codex CLI (`.codex/config.toml`)
```toml
[mcp_servers.sari]
command = "python3"
args = ["-m", "sari.mcp.cli", "proxy", "--daemon-port", "47800"]
```

### 3. IDEs (VS Code / Cursor / IntelliJ)
Use the `proxy` mode to connect to the global high-performance daemon.
- **Tool Command**: `python3 -m sari.mcp.cli proxy`
- **Recommended**: Start the daemon separately (`sari daemon start -d`) for maximum speed.

---

# Sari: 울트라 터보 검색 엔진 🚀 (Korean)

Sari는 **"울트라 터보"** 아키텍처로 완전히 재설계된 고성능 로컬 코드 검색 및 인덱싱 에이전트입니다. MCP(Model Context Protocol)를 통해 다양한 AI 도구와 완벽하게 연동됩니다.

## ⚡ 주요 개선 사항
- **병렬 파싱**: 모든 CPU 코어를 100% 활용하는 멀티프로세싱 엔진.
- **RAM 스테이징**: 메모리 기반 초고속 데이터 주입.
- **30GB MMAP**: 사실상 응답 지연이 없는(0ms) 검색 환경.
- **지능형 거버너**: 시스템 부하에 따라 0.3배 ~ 2.5배 속도 자동 조절.

## 🛠 도구 연동 가이드

### 1. Gemini CLI 연동 (`.gemini/settings.json`)
복잡한 설정은 사라졌습니다. 데몬 포트만 지정하면 모든 성능을 누릴 수 있습니다.
```json
{
  "mcpServers": {
    "sari": {
      "command": "python3",
      "args": ["-m", "sari.mcp.cli", "proxy", "--daemon-port", "47800"]
    }
  }
}
```

### 2. Codex CLI 연동 (`.codex/config.toml`)
```toml
[mcp_servers.sari]
command = "python3"
args = ["-m", "sari.mcp.cli", "proxy", "--daemon-port", "47800"]
```

### 3. IDE 연동 (VS Code / Cursor / IntelliJ)
Sari를 MCP 서버로 등록할 때 `proxy` 모드를 사용하세요.
- **실행 명령**: `python3 -m sari.mcp.cli proxy`
- **권장 사항**: 데몬을 미리 실행(`sari daemon start -d`)해두면 클라이언트 로딩 속도가 비약적으로 향상됩니다.

## 🚀 빠른 시작
```bash
# 고성능 환경 자동 구축
bash bootstrap.sh

# 터보 데몬 실행
python3 -m sari.mcp.cli daemon start -d
```
