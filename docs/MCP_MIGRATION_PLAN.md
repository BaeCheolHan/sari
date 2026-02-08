# MCP 전환 설계서 (FastMCP 기반)

목표: 현재 Sari의 MCP 통신 레이어를 **공식 Python MCP SDK(FastMCP)** 기반으로 전환하여
- stdio/HTTP 전송 계층 안정화
- 클라이언트 호환성 개선
- 프로토콜/프레이밍 문제 근본 제거

---

## 1. 현행 구조 요약

- stdio: `python -m sari --transport stdio` → **프록시 → 데몬**
- 데몬: TCP 기반 MCP 세션 처리 (`sari.mcp.session`, `sari.mcp.server`)
- Transport: 자체 구현 (`sari.mcp.transport`)
- 클라이언트별 문제: JSONL/Content-Length 혼용, 데몬/stdio 혼합

---

## 2. 목표 구조

- MCP 통신은 **FastMCP**로 통일
- stdio/HTTP/SSE/streamable-http는 SDK 레이어가 책임
- Sari는 **도메인 로직(검색/인덱싱/워크스페이스/DB)**에 집중

구조 개요:
```
Client
  └─ FastMCP server
        ├─ Tool registry (Sari tools)
        ├─ Workspace/Index engine
        └─ Logging (stderr/file only)
```

---

## 3. 전환 전략 (단계별)

### Phase 0: 준비
- 도구 스키마/입력/출력 일관성 점검
- stdout 오염 제거 점검
- 기존 transport 테스트를 **호환성 테스트**로 변환

### Phase 1: 병행 서버 추가 (feature flag)
- `SARI_FASTMCP=1` 또는 `--mcp-impl fast` 옵션 도입
- 기존 MCP 서버와 **동시에 유지**
- FastMCP 서버는 stdio 한정으로 시작

### Phase 2: 데몬 통합
- FastMCP로 데몬 내부 처리 대체
- 기존 `sari.mcp.session` 단계적으로 축소

### Phase 3: 기본값 전환
- FastMCP를 기본으로 사용
- 기존 transport는 fallback만 유지

---

## 4. 리스크 및 대응

### 리스크 A: 도구 스키마 호환성
- OpenAI/Codex 호환 문제 (integer → number 등)
- 대응: Serena 방식의 스키마 정규화 레이어 추가

### 리스크 B: 성능/동시성
- FastMCP 내부 스레딩 모델과 기존 병렬 처리 충돌
- 대응: 워커 큐/락 정책 재정의

### 리스크 C: 기존 클라이언트 호환
- 프록시/데몬 구조 유지 여부
- 대응: 단계별 전환 + feature flag 유지

---

## 5. 검증 기준

- Gemini/Codex stdio 연결 안정성
- 데몬 + 다중 클라이언트 동시 접속
- 270+ 테스트 통과
- 장시간 실행 시 메모리/락 문제 없음

---

## 6. 산출물

- FastMCP 기반 서버 구현
- 호환성 테스트 케이스
- 운영 문서 업데이트

