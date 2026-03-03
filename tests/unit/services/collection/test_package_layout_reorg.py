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
    from sari.services.collection.l5.lsp.broker_guard_service import LspBrokerGuardService
    from sari.services.collection.l5.lsp.symbol_normalizer_service import LspSymbolNormalizerService

    assert LspBrokerGuardService is not None
    assert LspSymbolNormalizerService is not None


def test_core_repo_context_importable_from_canonical_path() -> None:
    from sari.core.repo.context_resolver import RepoContextDTO

    assert RepoContextDTO is not None


def test_src_code_no_longer_imports_collection_legacy_shim_modules() -> None:
    src_root = Path(__file__).resolve().parents[4] / "src"
    banned_snippets = (
        "from sari.services.collection.l2_job_processor import",
        "from sari.services.collection.lsp import",
        "from sari.services.collection.l3_stages import",
        "from sari.services.collection.l3_language_processors import",
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


def test_src_code_no_longer_imports_core_legacy_shim_modules() -> None:
    src_root = Path(__file__).resolve().parents[4] / "src"
    banned_snippets = (
        "from sari.core.language_registry import",
        "from sari.core.lsp_provision_policy import",
        "from sari.core.repo_context_resolver import",
        "from sari.core.repo_identity import",
        "from sari.core.repo_resolver import",
    )
    violations: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for snippet in banned_snippets:
            if snippet in text:
                rel = path.relative_to(src_root)
                violations.append(f"{rel}: {snippet}")
    assert violations == []


def test_tests_use_layer_paths_not_legacy_collection_shims() -> None:
    tests_root = Path(__file__).resolve().parents[3]
    allow_legacy_import_tests = {
        "unit/services/collection/test_collection_layer_packages.py",
        "unit/services/collection/test_package_layout_reorg.py",
    }
    banned_modules = (
        "sari.services.collection.l2_job_processor",
        "sari.services.collection.event_watcher",
        "sari.services.collection.scanner",
        "sari.services.collection.watcher_hotness_tracker",
        "sari.services.collection.solid_lsp_extraction_backend",
        "sari.services.collection.solid_lsp_probe_mixin",
        "sari.services.collection.lsp",
        "sari.services.collection.l3_stages",
        "sari.services.collection.l3_language_processors",
    )
    violations: list[str] = []
    for path in tests_root.rglob("*.py"):
        rel = str(path.relative_to(tests_root))
        if rel in allow_legacy_import_tests:
            continue
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod in banned_modules:
                    violations.append(f"{rel}: {mod}")
                if mod.startswith("sari.services.collection.l3_"):
                    violations.append(f"{rel}: {mod}")
                if mod.startswith("sari.services.collection.l4_"):
                    violations.append(f"{rel}: {mod}")
                if mod.startswith("sari.services.collection.l5_"):
                    violations.append(f"{rel}: {mod}")
                if mod.startswith("sari.services.collection.lsp_"):
                    violations.append(f"{rel}: {mod}")
    assert violations == []


def test_collection_root_shim_files_are_removed() -> None:
    collection_root = Path(__file__).resolve().parents[4] / "src" / "sari" / "services" / "collection"
    shim_names = {"event_watcher.py", "scanner.py", "l2_job_processor.py"}
    shim_paths = list(collection_root.glob("l3_*.py"))
    shim_paths += list(collection_root.glob("l4_*.py"))
    shim_paths += list(collection_root.glob("l5_*.py"))
    shim_paths += list(collection_root.glob("lsp_*.py"))
    shim_paths += [collection_root / name for name in shim_names]
    remaining = [str(path.relative_to(collection_root)) for path in sorted(set(shim_paths)) if path.exists()]
    assert remaining == []


def test_collection_root_shim_packages_are_removed() -> None:
    collection_root = Path(__file__).resolve().parents[4] / "src" / "sari" / "services" / "collection"
    shim_dirs = ("lsp", "l3_stages", "l3_language_processors")
    remaining = [name for name in shim_dirs if (collection_root / name).exists()]
    assert remaining == []


def test_core_root_shim_files_are_removed() -> None:
    core_root = Path(__file__).resolve().parents[4] / "src" / "sari" / "core"
    shim_names = {
        "language_registry.py",
        "lsp_provision_policy.py",
        "repo_context_resolver.py",
        "repo_identity.py",
        "repo_resolver.py",
    }
    remaining = [name for name in sorted(shim_names) if (core_root / name).exists()]
    assert remaining == []


def test_tests_do_not_import_core_legacy_shims() -> None:
    tests_root = Path(__file__).resolve().parents[3]
    banned_modules = {
        "sari.core.language_registry",
        "sari.core.lsp_provision_policy",
        "sari.core.repo_context_resolver",
        "sari.core.repo_identity",
        "sari.core.repo_resolver",
    }
    violations: list[str] = []
    for path in tests_root.rglob("*.py"):
        rel = str(path.relative_to(tests_root))
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod in banned_modules:
                    violations.append(f"{rel}: {mod}")
    assert violations == []
