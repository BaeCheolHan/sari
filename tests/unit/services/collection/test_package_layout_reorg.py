from __future__ import annotations

import ast
from pathlib import Path


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


def test_src_code_no_longer_imports_collection_legacy_shim_modules() -> None:
    src_root = Path(__file__).resolve().parents[4] / "src"
    banned_snippets = (
        "from sari.services.collection.l2_job_processor import",
        "from sari.services.collection.l3_",
        "from sari.services.collection.l4_",
        "from sari.services.collection.l5_",
        "from sari.services.collection.lsp_",
    )
    violations: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for snippet in banned_snippets:
            if snippet in text:
                rel = path.relative_to(src_root)
                violations.append(f"{rel}: {snippet}")
    assert violations == []


def test_collection_root_shim_files_remain_reexport_only() -> None:
    collection_root = Path(__file__).resolve().parents[4] / "src" / "sari" / "services" / "collection"
    shim_names = {"event_watcher.py", "scanner.py", "l2_job_processor.py"}
    shim_paths = list(collection_root.glob("l3_*.py"))
    shim_paths += list(collection_root.glob("l4_*.py"))
    shim_paths += list(collection_root.glob("l5_*.py"))
    shim_paths += list(collection_root.glob("lsp_*.py"))
    shim_paths += [collection_root / name for name in shim_names]
    violations: list[str] = []

    for path in sorted(set(shim_paths)):
        if not path.exists():
            continue
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in module.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("sari.services.collection."):
                continue
            violations.append(str(path.relative_to(collection_root)))
            break

    assert violations == []
