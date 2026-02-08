# DDD 3단계: 서비스 계층 도입 (Stage 3)

이 문서는 MCP/CLI가 직접 DB/Indexer를 만지지 않도록, 서비스 계층을 도입하는 3단계 변경 사항을 정리합니다.

---

## 1. 적용 내용

### 1.1 신규 서비스
- `SearchService`
- `IndexService`
- `CallGraphService`

### 1.2 Interface 변경
- MCP `search`는 `SearchService` 사용
- MCP `scan_once`, `rescan`, `index_file`은 `IndexService` 사용
- MCP `call_graph`는 `CallGraphService` 사용

---

## 2. 효과

- Interface → Infrastructure 직접 호출 감소
- 정책/예외 처리의 중심을 Service에 집중
- 테스트/검증 단위가 명확해짐

---

## 3. 남은 과제

- 다른 MCP 도구들도 서비스 계층으로 단계적 이동
- CLI도 서비스 계층을 사용하도록 정리
- 서비스 계층의 인터페이스 안정화
