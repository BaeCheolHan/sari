# 버그 분류 기준 (SARI)

## 1. 데이터 정합성 (Data Integrity)
- DB 스키마 불일치, 컬럼명/타입 불일치
- 레포지토리 ↔ 도메인 모델 매핑 오류
- 데이터 손실/오염 위험

## 2. 통신/프로토콜 (Protocol/Transport)
- MCP stdio 프로토콜 파손
- JSON-RPC 핸드셰이크 불일치
- stdout 오염, content-length 파손

## 3. 동시성/락 (Concurrency/Locking)
- 멀티프로세스/스레드 동시 접근
- DB locked/transaction conflict
- 데몬/워커 동시 접근 경쟁 상태

## 4. 워크스페이스/스코프 (Workspace/Scope)
- 잘못된 루트 매핑
- 다중 워크스페이스 등록 실패
- root_id/rel_path 꼬임

## 5. 성능/리소스 (Performance/Resources)
- 인덱싱 지연, 메모리 과다
- 스캔/검색 응답 지연
- 로그 폭증

## 버그 등록 템플릿
- 분류: (1~5)
- 증상:
- 재현 단계:
- 기대 결과:
- 실제 결과:
- 영향 범위:
- 우선순위:
