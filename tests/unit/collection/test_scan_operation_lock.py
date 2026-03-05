from __future__ import annotations

from pathlib import Path

from sari.core.exceptions import CollectionError
from sari.services.collection.scan_operation_lock import ScanOperationLock


def test_scan_operation_lock_retries_with_backoff_then_acquires(tmp_path: Path) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def _lock(_file: object) -> None:
        calls["count"] += 1
        if calls["count"] < 3:
            raise BlockingIOError("locked")

    lock = ScanOperationLock(
        lock_path=tmp_path / ".scan.lock",
        max_attempts=4,
        backoff_base_sec=0.1,
        backoff_max_sec=0.2,
        sleep_fn=lambda sec: sleeps.append(sec),
        lock_fn=_lock,
        unlock_fn=lambda _file: None,
    )

    with lock.acquire(operation="scan_once", repo_root="/repo"):
        pass

    assert calls["count"] == 3
    assert sleeps == [0.1, 0.2]


def test_scan_operation_lock_raises_when_retries_exhausted(tmp_path: Path) -> None:
    lock = ScanOperationLock(
        lock_path=tmp_path / ".scan.lock",
        max_attempts=2,
        backoff_base_sec=0.01,
        backoff_max_sec=0.01,
        sleep_fn=lambda _sec: None,
        lock_fn=lambda _file: (_ for _ in ()).throw(BlockingIOError("busy")),
        unlock_fn=lambda _file: None,
    )

    try:
        with lock.acquire(operation="scan_once", repo_root="/repo"):
            pass
    except CollectionError as exc:
        assert exc.context.code == "ERR_SCAN_OPERATION_LOCK_BUSY"
    else:
        raise AssertionError("CollectionError expected when scan operation lock retries are exhausted")
