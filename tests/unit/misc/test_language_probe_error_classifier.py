"""language probe 오류 분류기 정책 기반 동작을 검증한다."""

from __future__ import annotations

import json

from sari.services.language_probe import error_classifier as classifier


def test_classifier_default_policy_behaves_as_before() -> None:
    """기본 정책 파일 기준 분류 결과는 기존 계약을 유지해야 한다."""
    classifier._load_error_policy.cache_clear()
    assert classifier.classify_lsp_error_code("ERR_LSP_DOCUMENT_SYMBOL_FAILED", "command not found: pyright") == "ERR_LSP_SERVER_MISSING"
    assert classifier.is_timeout_error("ERR_LSP_TIMEOUT", "anything") is True
    assert classifier.extract_missing_dependency("No such file or directory: pyright-langserver") == "pyright"
    classifier._load_error_policy.cache_clear()


def test_classifier_allows_policy_override_from_env(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """환경변수 정책 파일로 분류 규칙을 오버라이드할 수 있어야 한다."""
    policy_path = tmp_path / "probe-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "timeout_codes": ["ERR_MY_TIMEOUT"],
                "timeout_tokens": ["zzz-timeout"],
                "missing_server_tokens": ["my-missing-server"],
                "missing_dependency_rules": [{"dependency": "mydep", "tokens": ["mydep-token"]}],
                "server_binary_tokens": ["my-binary-missing"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SARI_LSP_PROBE_ERROR_POLICY_PATH", str(policy_path))
    classifier._load_error_policy.cache_clear()

    assert classifier.classify_lsp_error_code("ERR_X", "my-missing-server boom") == "ERR_LSP_SERVER_MISSING"
    assert classifier.is_timeout_error("ERR_X", "zzz-timeout happened") is True
    assert classifier.extract_missing_dependency("contains mydep-token here") == "mydep"
    assert classifier.extract_missing_dependency("contains my-binary-missing here") == "server_binary"
    classifier._load_error_policy.cache_clear()
