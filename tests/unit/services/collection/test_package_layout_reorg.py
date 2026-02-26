from __future__ import annotations


def test_core_repo_modules_importable_from_new_package_path() -> None:
    from sari.core.repo.context_resolver import RepoContextDTO, resolve_repo_context
    from sari.core.repo.identity import compute_repo_id
    from sari.core.repo.resolver import resolve_repo_root

    assert RepoContextDTO is not None
    assert callable(resolve_repo_context)
    assert callable(compute_repo_id)
    assert callable(resolve_repo_root)


def test_core_language_modules_importable_from_new_package_path() -> None:
    from sari.core.language.provision_policy import get_lsp_provision_policy
    from sari.core.language.registry import get_enabled_languages

    assert callable(get_lsp_provision_policy)
    assert callable(get_enabled_languages)


def test_collection_lsp_modules_importable_from_new_package_path() -> None:
    from sari.services.collection.lsp.broker_guard_service import LspBrokerGuardService
    from sari.services.collection.lsp.symbol_normalizer_service import LspSymbolNormalizerService

    assert LspBrokerGuardService is not None
    assert LspSymbolNormalizerService is not None


def test_legacy_import_paths_still_work_for_backward_compatibility() -> None:
    from sari.core.repo_context_resolver import RepoContextDTO
    from sari.services.collection.lsp_broker_guard_service import LspBrokerGuardService

    assert RepoContextDTO is not None
    assert LspBrokerGuardService is not None
