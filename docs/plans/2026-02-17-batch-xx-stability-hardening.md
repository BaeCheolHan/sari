# Batch-XX Stability Hardening Implementation Log

## Summary
- 리뷰 항목 기반 안정화 배치를 구현했다.
- 랭킹 가중치 외부화, LSP 정리 타임아웃 가드, documentSymbol 파싱 완화, ETA 신뢰도 보강, 스키마 버전 연동, L2 배치 메모리 상한을 반영했다.

## Implemented Checklist
- [x] 랭킹 가중치 하드코딩 제거 및 설정 주입
- [x] 랭킹 메타 확장(`ranking_stage`, `blend_config_version`)
- [x] importance 메모리 캐시 동시성 락 적용
- [x] LSP idle cleanup 정리 타임아웃 가드 추가
- [x] L2 본문 배치 버퍼 바이트 상한 도입
- [x] documentSymbol 파싱에서 `relativePath` 필수 의존 제거
- [x] ETA 계산에 EMA/신뢰도 지표 추가
- [x] bootstrap 모드 재진입(coverage drop) 보강
- [x] 스키마 버전 테이블과 마이그레이션 실행 경로 연결
- [x] 관련 단위/전체 테스트 통과

## Verification
- Targeted tests: `38 passed`
- Full test suite: `207 passed, 1 skipped`

