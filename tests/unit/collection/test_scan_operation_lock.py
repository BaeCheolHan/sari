from __future__ import annotations

import threading
import time
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


def test_scan_operation_lock_serializes_same_process_concurrency(tmp_path: Path) -> None:
    state = {"held": False}
    entered = threading.Event()
    errors: list[str] = []
    order: list[str] = []

    def _lock(_file: object) -> None:
        if state["held"]:
            raise BlockingIOError("busy")
        state["held"] = True

    def _unlock(_file: object) -> None:
        state["held"] = False

    lock = ScanOperationLock(
        lock_path=tmp_path / ".scan.lock",
        max_attempts=2,
        backoff_base_sec=0.01,
        backoff_max_sec=0.01,
        sleep_fn=lambda _sec: None,
        lock_fn=_lock,
        unlock_fn=_unlock,
    )

    def _first() -> None:
        try:
            with lock.acquire(operation="scan_once", repo_root="/repo"):
                order.append("first-enter")
                entered.set()
                time.sleep(0.05)
                order.append("first-exit")
        except CollectionError as exc:  # pragma: no cover - regression signal
            errors.append(exc.context.code)

    def _second() -> None:
        entered.wait(timeout=1.0)
        try:
            with lock.acquire(operation="scan_once", repo_root="/repo"):
                order.append("second-enter")
                order.append("second-exit")
        except CollectionError as exc:
            errors.append(exc.context.code)

    t1 = threading.Thread(target=_first)
    t2 = threading.Thread(target=_second)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
    assert order == ["first-enter", "first-exit", "second-enter", "second-exit"]


def test_scan_operation_lock_wait_timeout_overrides_attempt_budget(tmp_path: Path) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def _lock(_file: object) -> None:
        calls["count"] += 1
        if calls["count"] < 4:
            raise BlockingIOError("busy")

    lock = ScanOperationLock(
        lock_path=tmp_path / ".scan.lock",
        max_attempts=2,
        backoff_base_sec=0.1,
        backoff_max_sec=0.1,
        sleep_fn=lambda sec: sleeps.append(sec),
        lock_fn=_lock,
        unlock_fn=lambda _file: None,
    )

    with lock.acquire(operation="scan_once", repo_root="/repo", wait_timeout_sec=0.35):
        pass

    assert calls["count"] == 4
    assert sleeps == [0.1, 0.1, 0.1]


def test_scan_operation_lock_wait_timeout_includes_same_process_queueing(tmp_path: Path) -> None:
    order: list[str] = []
    errors: list[str] = []

    lock = ScanOperationLock(
        lock_path=tmp_path / ".scan.lock",
        max_attempts=2,
        backoff_base_sec=0.01,
        backoff_max_sec=0.01,
        sleep_fn=lambda _sec: None,
        lock_fn=lambda _file: None,
        unlock_fn=lambda _file: None,
    )

    def _first() -> None:
        with lock.acquire(operation="scan_once", repo_root="/repo", wait_timeout_sec=1.0):
            order.append("first-enter")
            time.sleep(0.2)
            order.append("first-exit")

    def _second() -> None:
        time.sleep(0.02)
        try:
            with lock.acquire(operation="scan_once", repo_root="/repo", wait_timeout_sec=0.05):
                order.append("second-enter")
        except CollectionError as exc:
            errors.append(exc.context.code)

    t1 = threading.Thread(target=_first)
    t2 = threading.Thread(target=_second)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert order == ["first-enter", "first-exit"]
    assert errors == ["ERR_SCAN_OPERATION_LOCK_BUSY"]


def test_scan_operation_lock_wait_timeout_counts_thread_and_file_lock_together(tmp_path: Path) -> None:
    sleeps: list[float] = []
    errors: list[str] = []
    order: list[str] = []
    busy_until = {"value": 0.0}

    def _lock(_file: object) -> None:
        if time.monotonic() < busy_until["value"]:
            raise BlockingIOError("busy")

    def _unlock(_file: object) -> None:
        return None

    lock = ScanOperationLock(
        lock_path=tmp_path / ".scan.lock",
        max_attempts=10,
        backoff_base_sec=0.03,
        backoff_max_sec=0.03,
        sleep_fn=lambda sec: (sleeps.append(sec), time.sleep(sec)),
        lock_fn=_lock,
        unlock_fn=_unlock,
    )

    def _first() -> None:
        with lock.acquire(operation="scan_once", repo_root="/repo", wait_timeout_sec=1.0):
            order.append("first-enter")
            busy_until["value"] = time.monotonic() + 0.2
            time.sleep(0.05)
            order.append("first-exit")

    def _second() -> None:
        time.sleep(0.01)
        try:
            with lock.acquire(operation="scan_once", repo_root="/repo", wait_timeout_sec=0.06):
                order.append("second-enter")
        except CollectionError as exc:
            errors.append(exc.context.code)

    t1 = threading.Thread(target=_first)
    t2 = threading.Thread(target=_second)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert order == ["first-enter", "first-exit"]
    assert errors == ["ERR_SCAN_OPERATION_LOCK_BUSY"]
    assert sum(sleeps) <= 0.06 + 0.02
