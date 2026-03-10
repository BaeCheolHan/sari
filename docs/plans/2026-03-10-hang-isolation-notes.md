# Hang Isolation Notes - 2026-03-10

## Summary
- `tests/unit/lsp/test_solidlsp_request_lifecycle.py` 단독 실행: 정상
- `tests/unit/mcp/test_status_language_support_contract.py` 단독 실행: 정상
- `tests/unit/lsp/test_lsp_hub_mapping.py` 전체 실행: 30초 timeout으로 종료

## Isolation Result
- `test_lsp_hub_mapping.py`의 원인 범위는 Java fallback 3개 테스트였다.
  - `test_lsp_hub_java_auto_fallback_retries_with_bundled_gradle`
  - `test_lsp_hub_java_indexing_prefers_bundled_gradle_first`
  - `test_lsp_hub_java_explicit_wrapper_setting_disables_auto_fallback`

## Important Finding
- 이 3개 테스트는 실제 hang가 아니라 각각 약 `23.50s`에 PASS 했다.
- 원인은 테스트가 실제 Java runtime 탐색(`LspRuntimeBroker.resolve(Language.JAVA, ...)`)을 타고 있었기 때문이다.
- 테스트 목적은 fallback env 분기 검증이지 런타임 탐색 검증이 아니므로, broker stub을 주입해 느린 외부 탐색을 제거했다.
- 수정 후:
  - 위 3개 테스트는 `3 passed in 0.33s`
- 즉 기존에 보이던 “hang”는 적어도 이 축에서는 deadlock이 아니라, **느린 외부 탐색 때문에 timeout처럼 보인 것**이었다.

## Reproduction Evidence

### Fast groups
```bash
uv run pytest -q tests/unit/lsp/test_solidlsp_request_lifecycle.py
# 2 passed in 0.34s

uv run pytest -q tests/unit/mcp/test_status_language_support_contract.py
# 8 passed in 3.28s
```

### Slow single-test cases (before fix)
```bash
uv run pytest -q tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_java_auto_fallback_retries_with_bundled_gradle
# 1 passed in 23.50s

uv run pytest -q tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_java_indexing_prefers_bundled_gradle_first
# 1 passed in 23.50s

uv run pytest -q tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_java_explicit_wrapper_setting_disables_auto_fallback
# 1 passed in 23.50s
```

### After fix
```bash
uv run pytest -q \
  tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_java_auto_fallback_retries_with_bundled_gradle \
  tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_java_indexing_prefers_bundled_gradle_first \
  tests/unit/lsp/test_lsp_hub_mapping.py::test_lsp_hub_java_explicit_wrapper_setting_disables_auto_fallback
# 3 passed in 0.33s
```

## Likely Cause
- Java fallback 테스트가 공통으로 거치는 `LspHub -> LspRuntimeBroker.resolve(Java)`가 실제 머신의 Java 후보를 탐색하고 `java -version` probe를 수행했다.
- 따라서 현재 우선순위는 “deadlock 수정”이 아니라:
  1. 느린 외부 탐색이 테스트에 섞이지 않게 stub/fake로 격리
  2. 전체 파일 실행시간이 여전히 긴 이유를 따로 분리
  3. 필요 시 slow test marker 또는 더 세밀한 분리 실행 전략 도입

## Updated Next Step
- `test_lsp_hub_mapping.py` 전체는 여전히 30초 timeout 래퍼 기준으로 초과할 수 있다.
- 그러나 현재 확인된 바에 따르면 이는 특정 deadlock이 아니라 파일 전체 누적 실행시간 문제에 더 가깝다.
- 다음 단계는:
  1. 전체 파일의 장기 테스트를 별도 그룹/marker로 분리할지 결정
  2. `busy/evict/cleanup/restart` 묶음과 Java/IO-heavy 묶음을 나눠 CI/로컬 검증 전략을 정리
