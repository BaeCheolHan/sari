# core package guide

`core`는 전역 정책/모델/식별 규칙을 담는 계층입니다.

## package layout

- `config.py`: 런타임 환경설정 로더
- `models*.py`: 공통 DTO/모델
- `exceptions.py`: 공통 예외 타입
- `repo/`: repo key/root/id 정규화 및 컨텍스트 해석
- `language/`: 언어 레지스트리 및 LSP 프로비저닝 정책

## rules

- 새 repo 관련 유틸은 `core/repo/`에 추가
- 새 언어 정책/레지스트리는 `core/language/`에 추가
- 기존 flat 경로 shim은 제거 완료
- canonical import만 사용:
  - `sari.core.repo.context_resolver`
  - `sari.core.repo.identity`
  - `sari.core.repo.resolver`
  - `sari.core.language.registry`
  - `sari.core.language.provision_policy`
