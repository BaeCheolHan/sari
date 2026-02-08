# Fail Fast 우선순위 영역

## 목표
즉시 실패/연쇄 장애를 유발하는 지점을 우선 제거해 체감 안정성을 빠르게 개선한다.

## 우선순위 1: 통신/프로토콜
- MCP stdio 오염 (stdout 출력, 로그 혼입)
- content-length 파손, JSON-RPC 핸드셰이크 실패
- 해결 원칙: stdout 금지, 디버그는 logger로 stderr/파일만

## 우선순위 2: DB 스키마 정합성
- 테이블/컬럼 불일치
- repository ↔ schema 매핑 오류
- 해결 원칙: schema 기준 1:1 매핑, 테스트로 검증

## 우선순위 3: 동시성/락
- 멀티프로세스 DB 접근, WAL 상태 불안정
- 데몬/워커 경쟁 상태
- 해결 원칙: 단일 writer 경로 고정, locked 재시도 가드

## 우선순위 4: 워크스페이스 등록
- workspace_root 잘못 인식
- multi-workspace 등록 실패
- 해결 원칙: registry 상태를 SSOT로 유지

## 체크리스트
- [ ] stdout 오염 제거 확인
- [ ] 스키마/레포 매핑 테스트 통과
- [ ] 멀티프로세스 쓰기 테스트 통과
- [ ] daemon ensure → workspace 등록 확인
