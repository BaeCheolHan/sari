from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sari.core.models import CollectionScanResultDTO
from sari.services.collection.service import FileCollectionService


def test_scan_once_uses_scan_operation_lock(tmp_path: Path) -> None:
    service = FileCollectionService.__new__(FileCollectionService)
    calls: list[tuple[str, str, float | None]] = []

    class _Resolver:
        def resolve_targets(self, _root: Path) -> list[Path]:
            return []

    @contextmanager
    def _acquire(*, operation: str, repo_root: str, wait_timeout_sec: float | None = None):
        calls.append((operation, repo_root, wait_timeout_sec))
        yield

    service._fanout_resolver = _Resolver()
    service._scan_operation_lock = type("Lock", (), {"acquire": staticmethod(_acquire)})()
    service._lsp_backend = object()
    service._cleanup_stale_fanout_rows_for_single_repo = lambda *, root_path: None
    service._scanner_scan_once = lambda **kwargs: CollectionScanResultDTO(scanned_count=1, indexed_count=1, deleted_count=0)

    _ = FileCollectionService.scan_once(service, str(tmp_path))

    assert len(calls) == 1
    assert calls[0][0] == "scan_once"
    assert calls[0][1] == str(tmp_path.resolve())
    assert calls[0][2] == FileCollectionService.SCAN_OPERATION_LOCK_MANUAL_WAIT_TIMEOUT_SEC


def test_background_scan_once_keeps_default_lock_wait_budget(tmp_path: Path) -> None:
    service = FileCollectionService.__new__(FileCollectionService)
    calls: list[tuple[str, str, float | None]] = []

    class _Resolver:
        def resolve_targets(self, _root: Path) -> list[Path]:
            return []

    @contextmanager
    def _acquire(*, operation: str, repo_root: str, wait_timeout_sec: float | None = None):
        calls.append((operation, repo_root, wait_timeout_sec))
        yield

    service._fanout_resolver = _Resolver()
    service._scan_operation_lock = type("Lock", (), {"acquire": staticmethod(_acquire)})()
    service._lsp_backend = object()
    service._cleanup_stale_fanout_rows_for_single_repo = lambda *, root_path: None
    service._scanner_scan_once = lambda **kwargs: CollectionScanResultDTO(scanned_count=1, indexed_count=1, deleted_count=0)

    _ = FileCollectionService.scan_once(service, str(tmp_path), trigger="background")

    assert calls == [("scan_once", str(tmp_path.resolve()), None)]


def test_index_file_uses_scan_operation_lock(tmp_path: Path) -> None:
    service = FileCollectionService.__new__(FileCollectionService)
    calls: list[tuple[str, str, float | None]] = []
    scanner_calls: list[dict[str, str]] = []

    @contextmanager
    def _acquire(*, operation: str, repo_root: str, wait_timeout_sec: float | None = None):
        calls.append((operation, repo_root, wait_timeout_sec))
        yield

    service._scan_operation_lock = type("Lock", (), {"acquire": staticmethod(_acquire)})()
    service._lsp_backend = object()

    def _scanner_index_file(*, repo_root: str, relative_path: str, scope_repo_root: str) -> CollectionScanResultDTO:
        scanner_calls.append(
            {
                "repo_root": repo_root,
                "relative_path": relative_path,
                "scope_repo_root": scope_repo_root,
            }
        )
        return CollectionScanResultDTO(scanned_count=1, indexed_count=1, deleted_count=0)

    service._scanner_index_file = _scanner_index_file

    _ = FileCollectionService.index_file(service, str(tmp_path), "src/main.py")

    assert calls == [("index_file", str(tmp_path.resolve()), FileCollectionService.SCAN_OPERATION_LOCK_MANUAL_WAIT_TIMEOUT_SEC)]
    assert scanner_calls == [
        {
            "repo_root": str(tmp_path.resolve()),
            "relative_path": "src/main.py",
            "scope_repo_root": str(tmp_path.resolve()),
        }
    ]
