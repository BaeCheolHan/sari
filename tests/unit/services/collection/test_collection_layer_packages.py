from __future__ import annotations

from pathlib import Path


def test_l1_package_imports() -> None:
    from sari.services.collection.l1 import EventWatcher as EventWatcherFromInit
    from sari.services.collection.l1 import FileScanner as FileScannerFromInit
    from sari.services.collection.l1.scanner import FileScanner
    from sari.services.collection.l1.event_watcher import EventWatcher

    assert FileScanner is not None
    assert EventWatcher is not None
    assert FileScannerFromInit is FileScanner
    assert EventWatcherFromInit is EventWatcher


def test_l2_package_imports() -> None:
    from sari.services.collection.l2.job_processor import L2JobProcessor

    assert L2JobProcessor is not None


def test_l3_package_imports() -> None:
    from sari.services.collection.l3.orchestrator import L3Orchestrator
    from sari.services.collection.l3.asset_loader import L3AssetLoader

    assert L3Orchestrator is not None
    assert L3AssetLoader is not None


def test_l4_package_imports() -> None:
    from sari.services.collection.l4.admission_service import L4AdmissionService

    assert L4AdmissionService is not None


def test_l5_package_imports() -> None:
    from sari.services.collection.l5 import L5AdmissionPolicy as L5AdmissionPolicyFromInit
    from sari.services.collection.l5.lsp import LspSessionBroker as LspSessionBrokerFromInit
    from sari.services.collection.l5.admission_policy import L5AdmissionPolicy
    from sari.services.collection.l5.lsp.session_broker import LspSessionBroker

    assert L5AdmissionPolicy is not None
    assert LspSessionBroker is not None
    assert L5AdmissionPolicyFromInit is L5AdmissionPolicy
    assert LspSessionBrokerFromInit is LspSessionBroker


def test_legacy_import_shims_still_work() -> None:
    from sari.services.collection.l3_orchestrator import L3Orchestrator
    from sari.services.collection.lsp_session_broker import LspSessionBroker

    assert L3Orchestrator is not None
    assert LspSessionBroker is not None


def test_collection_entrypoints_prefer_canonical_layer_imports() -> None:
    root = Path(__file__).resolve().parents[4] / "src" / "sari" / "services" / "collection"
    service_source = (root / "service.py").read_text(encoding="utf-8")
    enrich_source = (root / "enrich_engine.py").read_text(encoding="utf-8")

    assert "from sari.services.collection.l1.event_watcher import EventWatcher" in service_source
    assert "from sari.services.collection.l1.scanner import FileScanner" in service_source
    assert "from sari.services.collection.l5.lsp.session_broker import" in service_source
    assert "from sari.services.collection.l5.solid_lsp_extraction_backend import" in service_source

    assert "from sari.services.collection.l3.l3_orchestrator import L3Orchestrator" in enrich_source
    assert "from sari.services.collection.l5.l5_admission_policy import" in enrich_source
