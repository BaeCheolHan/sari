# Unit Test Packages

`tests/unit`는 도메인별 패키지로 분리되어 있다.

- `admin`: 관리자/버전 정합성
- `ci`: CI·릴리즈 게이트 스크립트/워크플로우 계약
- `cli`: CLI 명령 경로
- `collection`: 수집/저장소/enrich 계층
- `daemon`: 데몬/프록시/레지스트리
- `http_api`: HTTP 엔드포인트/응답 계층
- `l3`: L3 추출·전처리·스케줄링
- `l5`: L5 admission/policy/runtime
- `lsp`: LSP 런타임/어댑터/정규화
- `mcp`: MCP 도구/프로토콜/안정화
- `misc`: 아키텍처/계약/기타 회귀
- `pipeline`: 파이프라인 공통 제어/지표/정책
- `pipeline_lsp`: LSP matrix 파이프라인
- `pipeline_perf`: 성능 파이프라인/추적
- `pipeline_quality`: L3-LSP-Golden 품질 측정
- `search`: 검색/후보 선택
- `services`: 서비스 계층 패키지 구조 테스트
- `workspace`: 워크스페이스/리포 컨텍스트/런타임 저장소
