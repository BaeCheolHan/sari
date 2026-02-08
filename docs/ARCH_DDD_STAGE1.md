# DDD 1단계: 경계 분리 설계 (Stage 1)

이 문서는 Sari의 현재 구조를 DDD 관점으로 분류하고, 경계 위반 지점을 정리한 1단계 설계 문서입니다.

---

## 1. 경계 분류 기준

- **Domain**: 순수 모델/규칙. 외부 I/O, DB, 네트워크에 의존하지 않음.
- **Application**: 유스케이스/서비스. 도메인 조합과 흐름 제어.
- **Infrastructure**: DB, 엔진, 파일 I/O, 외부 프로세스/네트워크.
- **Interface**: MCP/HTTP/CLI 등 외부 입출력 경계.

---

## 2. 현재 모듈 분류

### 2.1 Domain (순수 모델)
- `src/sari/core/models.py` (SearchHit, SearchOptions)
- `src/sari/core/indexer/main.py`의 `IndexStatus`

### 2.2 Application (유스케이스)
- (현재는 뚜렷한 Service 계층이 없음)
- 다음 항목을 Application으로 분리할 필요가 있음:
  - 검색 유스케이스: Search 흐름 제어
  - 인덱싱 유스케이스: Scan/Rescan/Index_file 흐름 제어
  - 그래프 유스케이스: call_graph/callers/implementations

### 2.3 Infrastructure
- DB/스토리지: `src/sari/core/db/*`
- 엔진: `src/sari/core/engine/*`, `src/sari/core/engine_runtime.py`
- 파일/FS: `src/sari/core/watcher.py`, `src/sari/core/utils/*`
- 스케줄러/큐: `src/sari/core/scheduler/*`, `src/sari/core/queue_pipeline.py`
- HTTP 서버: `src/sari/core/http_server.py`, `src/sari/core/async_http_server.py`

### 2.4 Interface
- MCP 서버/도구: `src/sari/mcp/*`
- CLI: `src/sari/main.py`, `src/sari/mcp/cli.py`

---

## 3. 경계 위반(현재 상태)

### 3.1 Interface → Infrastructure 직접 접근
- MCP tools가 DB/엔진에 직접 접근 (예: `search.py`, `read_file.py` 등)
- MCP/CLI에서 DB/Indexer 직접 생성

### 3.2 Interface → Domain 누락
- 도메인 모델이 단순 DTO 역할만 수행
- 응답/검증/정렬 로직이 도구 레벨에 분산

### 3.3 Infrastructure → Interface 의존
- `core/health.py`가 CLI 함수 호출
- `core/main.py`가 MCP 서버 직접 생성

---

## 4. 1단계 목표

- **Application Service 계층 신설**
  - `SearchService`
  - `IndexService`
  - `CallGraphService`
- Interface(MCP/CLI)는 Service만 호출
- Infrastructure는 Service 뒤로 숨김

---

## 5. 다음 단계 준비

- 2단계: 도메인 모델 캡슐화 (행동/검증 내장)
- 3단계: 서비스 계층 구축 및 MCP/CLI 연결 변경

