# Transport 확장 로드맵 (SSE/Streamable-HTTP)

목표: stdio + 데몬 안정화 이후, **SSE/Streamable-HTTP** 지원을 추가한다.

---

## 1. 배경

- 현재 Sari는 `stdio`, `http`만 지원
- Serena는 `stdio`, `sse`, `streamable-http` 지원
- HTTP 기반 MCP는 IDE/웹 UI 통합에 필수

---

## 2. 단계별 로드맵

### 단계 1: 설계 정리
- SSE vs streamable-http의 사용 시나리오 구분
- 기존 HTTP API와 MCP endpoint 분리 여부 결정

### 단계 2: 프로토타입
- FastMCP 기반 SSE/streamable-http 서버 구현
- stdio와 동일 툴셋 공유

### 단계 3: 인증/보안
- 로컬 전용(loopback) 정책 유지
- 추후 토큰 기반 인증 옵션 설계

### 단계 4: 운영 문서/도구 업데이트
- README 및 설치 가이드 업데이트
- CLI 옵션 추가

---

## 3. 성공 기준

- Gemini/Codex 외 클라이언트 연결 가능
- SSE 연결 지속성/복구 테스트 통과
- 동시에 stdio와 HTTP 혼재 운용 가능

