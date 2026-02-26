# services.collection package guide

`services.collection`은 인덱싱 파이프라인(L1-L5)의 실행 계층입니다.

## package layout

- `l1/`: 파일 스캔/FS 이벤트 감지
- `l2/`: L2 큐 잡 처리
- `l3/`: Tree-sitter 전처리/판정/저장/품질 측정
: `l3/stages/`, `l3/language_processors/`, `l3/assets/` 포함
- `l4/`: L4 admission/게이트 서비스
- `l5/`: L5 정책/LSP 추출 본체
: `l5/lsp/`에 LSP 보조 서비스(브로커/정규화/parallelism) 포함
- `enrich_*`: EnrichEngine 런타임 조합/플러시/DTO(레이어 오케스트레이션)
- `service.py`: 수집 서비스 엔트리
- `solid_lsp_extraction_backend.py`: 하위 호환 shim (실체는 `l5/`)

## rules

- L1~L5 신규 구현은 각 레이어 폴더에만 추가
- LSP 보조 로직은 `collection/l5/lsp/`에 추가
- L3 stage는 `collection/l3/stages/`에 추가
- root의 `l3_*.py`, `l4_*.py`, `l5_*.py`, `lsp_*.py`는 하위 호환 shim입니다
