"""LSP 프로비저닝 정책 SSOT를 검증한다."""

from __future__ import annotations

from sari.core.language.registry import get_enabled_language_names
from sari.core.language.provision_policy import get_lsp_provision_policy


def test_lsp_provision_policy_covers_enabled_languages() -> None:
    """활성 언어는 모두 provisioning 정책을 반환해야 한다."""
    for language in get_enabled_language_names():
        policy = get_lsp_provision_policy(language)
        assert policy.language == language
        assert policy.provisioning_mode in {"auto_provision", "requires_system_binary", "hybrid"}
        assert policy.install_hint.strip() != ""

