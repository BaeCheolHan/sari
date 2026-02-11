# Unified Read v1 Stabilization Checklist

## Scope
- [ ] `read` 단일 엔트리포인트(`file|symbol|snippet|diff_preview`) 동작 확인
- [ ] `against` 범위 `HEAD|WORKTREE|INDEX`만 허용 확인
- [ ] 신규 MCP 도구 추가 없음 확인 (`read/search` 응답 확장만)

## Stabilization Primitives
- [ ] Session Metrics 카운터 구현/갱신
- [ ] Read Budget Guard 소프트 제한 구현
- [ ] Read Budget Guard 하드 제한(`BUDGET_EXCEEDED`) 구현
- [ ] Relevance Guard soft 정책(`LOW_RELEVANCE` + 대안 제시) 구현
- [ ] Auto-Aggregation v1-lite(중복 제거 + 구조적 압축) 구현

## Response Contract
- [ ] `read` 응답에 `meta.stabilization` 포함
- [ ] `search` 응답에 `meta.stabilization` 포함
- [ ] `meta.stabilization` 필드 결정론성 확인

## Backward Compatibility
- [ ] `read_file` wrapper -> `read(mode=file)`
- [ ] `read_symbol` wrapper -> `read(mode=symbol)`
- [ ] `get_snippet` wrapper -> `read(mode=snippet)`
- [ ] `dry_run_diff` wrapper -> `read(mode=diff_preview)`
- [ ] 레거시 응답 호환성 테스트 통과

## Tests (Must-have)
- [ ] budget soft/hard limit tests
- [ ] relevance guard hit/miss tests
- [ ] aggregation dedupe deterministic tests
- [ ] metrics counting deterministic tests

## Verification Gate
- [ ] `python3 -m ruff check src tests`
- [ ] targeted pytest set 통과
- [ ] full `pytest -q` 통과
- [ ] design/implementation 문서 간 불일치 없음
