from __future__ import annotations


def test_l1_package_imports() -> None:
    from sari.services.collection.l1.scanner import FileScanner
    from sari.services.collection.l1.event_watcher import EventWatcher

    assert FileScanner is not None
    assert EventWatcher is not None


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
    from sari.services.collection.l5.admission_policy import L5AdmissionPolicy
    from sari.services.collection.l5.lsp.session_broker import LspSessionBroker

    assert L5AdmissionPolicy is not None
    assert LspSessionBroker is not None


def test_legacy_import_shims_still_work() -> None:
    from sari.services.collection.l3_orchestrator import L3Orchestrator
    from sari.services.collection.lsp_session_broker import LspSessionBroker

    assert L3Orchestrator is not None
    assert LspSessionBroker is not None
