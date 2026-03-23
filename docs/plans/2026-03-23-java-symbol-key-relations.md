# Java Symbol-Key Relation Redesign

## Summary

현재 Java relation 저장은 `from_symbol` / `to_symbol` 문자열 중심이라 constructor, class, overloaded method가 뭉개진다. 대표 증상은 constructor caller가 `to_symbol="<init>"`로 저장되어 `get_callers(CommonMessageService)` 같은 클래스 질의에서 직접 조회되지 않는 문제다.

다음 단계 목표는 relation을 `symbol_key` 중심으로 재설계해서 클래스/생성자/메서드 구분을 저장 시점부터 유지하는 것이다. 이번 문서는 그 구현을 바로 시작할 수 있는 기준안을 고정한다.

## Target Model

- `lsp_call_relations` 후속 스키마는 다음 필드를 기준으로 잡는다.
  - `from_symbol_key`
  - `to_symbol_key`
  - `from_name`
  - `to_name`
  - `from_kind`
  - `to_kind`
  - `repo_root`
  - `relative_path`
  - `content_hash`
  - `line`
  - `created_at`
- `from_name` / `to_name`는 display cache로만 쓰고, 조회/동일성 판단은 `symbol_key` 우선이다.
- constructor는 `<init>` 같은 문자열 alias가 아니라 constructor symbol의 실제 `symbol_key`를 저장한다.
- overloaded method는 `name + signature-ish discriminator + line`을 반영한 `symbol_key`로 구분한다.

## Query Semantics

- `get_callers`, `call_graph`, `get_implementations`는 입력에 `symbol_key`가 있으면 그것을 최우선 사용한다.
- `symbol` 문자열 입력만 들어온 경우에는 먼저 `lsp_symbols`에서 후보를 resolve한 뒤 relation 조회로 연결한다.
- 클래스 질의에서 constructor caller를 보여줄지 여부는 조회 시 alias로 푸는 것이 아니라, resolve 단계에서 “class symbol이 가리키는 constructor symbol”을 추가 후보로 확장하는 방식으로 처리한다.
- 같은 이름의 동명이인 심볼은 `repo_root + relative_path + symbol_key` 기준으로 분리한다.

## Migration Strategy

- 1차 migration은 additive로 간다.
  - 기존 `from_symbol` / `to_symbol` 유지
  - 새 `from_symbol_key` / `to_symbol_key` 컬럼 추가
- 새 extractor는 symbol-key를 채우고, 기존 조회는 fallback으로 문자열 컬럼도 계속 읽는다.
- MCP 도구는 초기에는 wire shape를 바꾸지 않고, 내부 resolve만 symbol-key 우선으로 바꾼다.
- 저장 데이터가 모두 채워진 뒤에만 문자열-only fallback 제거 여부를 판단한다.

## Tests and Acceptance

- Java constructor/class/method가 별도 `symbol_key`로 저장되는지 단위 테스트 추가
- overloaded method 2개가 서로 다른 relation target으로 조회되는지 테스트 추가
- `get_callers(CommonMessageService)`와 `get_callers(<constructor symbol_key>)`가 같은 caller 집합을 안정적으로 반환하는지 검증
- fresh DB recollect 기준으로 `payment-service` Java relation 조회가 문자열 alias 없이도 복원되는지 확인
- Python 및 semantic edge 경로는 기존 결과가 바뀌지 않아야 한다

## Assumptions

- relation identity는 이름 문자열이 아니라 symbol identity로 옮기는 것이 장기 정답이다.
- constructor/class alias는 저장 시점이 아니라 resolve/query 단계에서 다루는 편이 migration risk가 낮다.
- 이번 재설계는 Java가 직접 동기가 되었지만, 스키마는 다른 언어에도 재사용 가능하게 유지한다.
