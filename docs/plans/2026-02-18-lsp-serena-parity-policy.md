# 2026-02-18 LSP Serena Parity Policy

## 목표
- `sari`의 LSP 기동/진단 정책을 `serena`와 동일한 혼합형 모델로 고정한다.
- LSP 실패 시 침묵 성공 없이 명시 오류 + 복구 힌트를 일관 제공한다.

## 결정사항
- 프로비저닝 정책 SSOT를 `sari.core.lsp_provision_policy`로 단일화한다.
- 정책 모드는 `auto_provision`, `requires_system_binary`, `hybrid` 세 가지로 제한한다.
- `LanguageProbeService`는 언어별 probe 결과에 `provisioning_mode`, `missing_dependency`, `install_hint`를 포함한다.
- `LspMatrixDiagnoseService`는 정책 요약(`language_policies`)과 복구 힌트가 포함된 권장 조치를 생성한다.
- `pack1_error`는 `structuredContent.error` 및 `meta.errors[*].recovery_hint`를 지원한다.
- HTTP 변환 계층은 `recovery_hint`를 JSON 오류 응답으로 전달한다.

## 패키징 정합화
- `pyproject.toml` 기본 의존성에 `pyright>=1.1.396,<2`를 추가해 Python LSP 기동 계약을 serena와 맞춘다.

## 검증 항목
- 단위 테스트:
  - `test_lsp_provision_policy.py`
  - `test_language_probe_service.py`
  - `test_lsp_matrix_diagnose_service.py`
  - `test_http_response_builders.py`
- 기존 LSP matrix/diagnose 회귀 테스트 통과 확인.
