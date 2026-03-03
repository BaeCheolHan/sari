"""서비스 패키지 레이아웃 정리 회귀 테스트."""

from __future__ import annotations

from pathlib import Path


def test_language_probe_modules_exist_under_dedicated_package() -> None:
    """language_probe 모듈들이 하위 패키지 경로로 import 가능해야 한다."""
    from sari.services.language_probe.error_classifier import classify_lsp_error_code
    from sari.services.language_probe.file_sampler import LanguageProbeFileSampler
    from sari.services.language_probe.service import LanguageProbeService
    from sari.services.language_probe.thread_runner import LanguageProbeThreadRunner
    from sari.services.language_probe.worker import LanguageProbeWorker

    assert callable(classify_lsp_error_code)
    assert LanguageProbeFileSampler is not None
    assert LanguageProbeService is not None
    assert LanguageProbeThreadRunner is not None
    assert LanguageProbeWorker is not None


def test_core_services_exist_under_packaged_paths() -> None:
    """core 서비스들은 패키지 경로로 import 가능해야 한다."""
    from sari.services.admin.service import AdminService
    from sari.services.collection.service import FileCollectionService
    from sari.services.daemon.service import DaemonService
    from sari.services.workspace.service import WorkspaceService

    assert AdminService is not None
    assert FileCollectionService is not None
    assert DaemonService is not None
    assert WorkspaceService is not None


def test_entrypoints_use_packaged_service_imports() -> None:
    """주요 엔트리포인트는 패키지 서비스 경로를 사용해야 한다."""
    root = Path(__file__).resolve().parents[3] / "src" / "sari"
    targets = [
        root / "cli" / "main.py",
        root / "mcp" / "server.py",
        root / "http" / "context.py",
    ]
    required_tokens = {
        root / "cli" / "main.py": [
            "from sari.services.admin import",
            "from sari.services.daemon import",
            "from sari.services.workspace import",
            "from sari.services.collection.service import",
        ],
        root / "mcp" / "server.py": [
            "from sari.services.admin import",
            "from sari.services.collection.service import",
        ],
        root / "http" / "context.py": [
            "from sari.services.admin import",
        ],
    }
    for path in targets:
        source = path.read_text(encoding="utf-8")
        for token in required_tokens[path]:
            assert token in source


def test_runtime_modules_use_packaged_service_imports() -> None:
    """런타임/도구 모듈도 패키지 서비스 경로를 사용해야 한다."""
    root = Path(__file__).resolve().parents[3] / "src" / "sari"
    required_tokens = {
        root / "daemon_process.py": [
            "from sari.services.admin import",
            "from sari.services.collection.service import",
        ],
        root / "mcp" / "daemon_forward_policy.py": [
            "from sari.services.daemon import",
        ],
        root / "mcp" / "tools" / "admin_tools.py": [
            "from sari.services.admin import",
        ],
    }
    for path, tokens in required_tokens.items():
        source = path.read_text(encoding="utf-8")
        for token in tokens:
            assert token in source


def test_src_avoids_legacy_service_imports() -> None:
    """src 코드에서 legacy 서비스 import를 금지한다."""
    root = Path(__file__).resolve().parents[3] / "src" / "sari"
    forbidden = (
        "from sari.services.admin_service import",
        "from sari.services.daemon_service import",
        "from sari.services.workspace_service import",
        "from sari.services.file_collection_service import",
        "from sari.services.language_probe_service import",
        "from sari.services.lsp_matrix_diagnose_service import",
        "from sari.services.pipeline_control_service import",
        "from sari.services.pipeline_lsp_matrix_service import",
        "from sari.services.pipeline_perf_service import",
        "from sari.services.pipeline_quality_service import",
        "from sari.services.read_facade_service import",
        "from sari.services.pipeline_ab_report import",
        "from sari.services.pipeline_lsp_matrix_ports import",
        "import sari.services.admin_service",
        "import sari.services.daemon_service",
        "import sari.services.workspace_service",
        "import sari.services.file_collection_service",
        "import sari.services.language_probe_service",
        "import sari.services.lsp_matrix_diagnose_service",
        "import sari.services.pipeline_control_service",
        "import sari.services.pipeline_lsp_matrix_service",
        "import sari.services.pipeline_perf_service",
        "import sari.services.pipeline_quality_service",
        "import sari.services.read_facade_service",
        "import sari.services.pipeline_ab_report",
        "import sari.services.pipeline_lsp_matrix_ports",
    )
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source


def test_legacy_service_modules_removed() -> None:
    """legacy shim 파일은 제거되어야 한다."""
    root = Path(__file__).resolve().parents[3] / "src" / "sari" / "services"
    assert not (root / "admin_service.py").exists()
    assert not (root / "daemon_service.py").exists()
    assert not (root / "workspace_service.py").exists()
    assert not (root / "file_collection_service.py").exists()
    assert not (root / "language_probe_service.py").exists()
    assert not (root / "lsp_matrix_diagnose_service.py").exists()
    assert not (root / "pipeline_control_service.py").exists()
    assert not (root / "pipeline_lsp_matrix_service.py").exists()
    assert not (root / "pipeline_perf_service.py").exists()
    assert not (root / "pipeline_quality_service.py").exists()
    assert not (root / "read_facade_service.py").exists()
    assert not (root / "pipeline_ab_report.py").exists()
    assert not (root / "pipeline_lsp_matrix_ports.py").exists()
    assert not (root / "language_probe_error_classifier.py").exists()
    assert not (root / "language_probe_file_sampler.py").exists()
    assert not (root / "language_probe_thread_runner.py").exists()
    assert not (root / "language_probe_worker.py").exists()
