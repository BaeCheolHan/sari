"""L5AsyncUpgradeWatcher 단위 테스트."""

from __future__ import annotations

import threading
import time

import pytest
from solidlsp.ls_config import Language

from sari.core.event_bus import EventBus
from sari.core.events import L3FlushCompleted, LspWarmReady
from sari.services.collection.l5.upgrade_watcher import L5AsyncUpgradeWatcher


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

class _FakeEnrichQueueRepo:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    def enqueue(self, **kwargs) -> None:
        self.enqueued.append(dict(kwargs))


class _FakeToolLayerRepo:
    def __init__(
        self,
        files: list[dict] | None = None,
        stale_count: int = 0,
    ) -> None:
        self._files = files or []
        self._stale_count = stale_count

    def list_l5_upgrade_candidates(self, *, workspace_id: str, repo_root: str, limit: int) -> list[dict]:
        return self._files[:limit]

    def count_l5_stale(self, *, workspace_id: str, repo_root: str) -> int:
        return self._stale_count


def _watcher(
    bus: EventBus,
    enrich_repo: _FakeEnrichQueueRepo | None = None,
    tool_repo: _FakeToolLayerRepo | None = None,
    enabled: bool = True,
    poll_interval_sec: float = 0.05,
) -> L5AsyncUpgradeWatcher:
    return L5AsyncUpgradeWatcher(
        event_bus=bus,
        enrich_queue_repo=enrich_repo or _FakeEnrichQueueRepo(),
        tool_layer_repo=tool_repo or _FakeToolLayerRepo(),
        workspace_id="",
        batch_size=50,
        poll_interval_sec=poll_interval_sec,
        enabled=enabled,
    )


def _wait(condition: object, *, timeout: float = 2.0, interval: float = 0.02) -> None:
    """condition()이 True가 될 때까지 polling으로 대기."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(condition) and condition():
            return
        time.sleep(interval)
    raise TimeoutError("condition not met within timeout")


# ---------------------------------------------------------------------------
# enabled=False 테스트
# ---------------------------------------------------------------------------

def test_start_does_nothing_when_disabled() -> None:
    """enabled=False 이면 start()가 스레드를 시작하지 않는다."""
    bus = EventBus()
    w = _watcher(bus, enabled=False)
    w.start()

    assert w._thread is None
    bus.shutdown()


def test_trigger_startup_does_nothing_when_disabled() -> None:
    """enabled=False 이면 trigger_startup()이 아무 것도 하지 않는다."""
    bus = EventBus()
    tool_repo = _FakeToolLayerRepo(stale_count=10)
    w = _watcher(bus, tool_repo=tool_repo, enabled=False)

    # 예외 없이 실행되어야 함
    w.trigger_startup(repo_root="/repo")
    bus.shutdown()


# ---------------------------------------------------------------------------
# LspWarmReady → 활성화 + 즉시 처리
# ---------------------------------------------------------------------------

def test_lsp_warm_ready_activates_repo_and_triggers_enqueue() -> None:
    """LspWarmReady 수신 시 repo가 activated되고 즉시 enqueue된다."""
    bus = EventBus()
    enrich_repo = _FakeEnrichQueueRepo()
    files = [
        {"repo_root": "/repo", "relative_path": "a.py", "content_hash": "h1"},
    ]
    tool_repo = _FakeToolLayerRepo(files=files)
    w = _watcher(bus, enrich_repo=enrich_repo, tool_repo=tool_repo)
    w.start()

    bus.publish(LspWarmReady(repo_root="/repo", language=Language.PYTHON))

    _wait(lambda: len(enrich_repo.enqueued) >= 1)

    assert enrich_repo.enqueued[0]["relative_path"] == "a.py"
    assert enrich_repo.enqueued[0]["enqueue_source"] == "l5"
    assert enrich_repo.enqueued[0]["priority"] == 20

    bus.shutdown()


# ---------------------------------------------------------------------------
# L3FlushCompleted — 미활성 무시
# ---------------------------------------------------------------------------

def test_l3_flush_ignored_when_repo_not_activated() -> None:
    """아직 LspWarmReady를 받지 않은 repo의 L3FlushCompleted는 무시된다."""
    bus = EventBus()
    enrich_repo = _FakeEnrichQueueRepo()
    files = [{"repo_root": "/repo", "relative_path": "b.py", "content_hash": "h2"}]
    tool_repo = _FakeToolLayerRepo(files=files)
    w = _watcher(bus, enrich_repo=enrich_repo, tool_repo=tool_repo, poll_interval_sec=100.0)
    w.start()

    # activated 없이 L3FlushCompleted 발행
    bus.publish(L3FlushCompleted(repo_root="/repo", flushed_count=1))

    time.sleep(0.1)
    assert len(enrich_repo.enqueued) == 0

    bus.shutdown()


# ---------------------------------------------------------------------------
# L3FlushCompleted — 활성화 상태에서 처리
# ---------------------------------------------------------------------------

def test_l3_flush_triggers_enqueue_when_repo_activated() -> None:
    """활성화된 repo의 L3FlushCompleted 수신 시 enqueue가 실행된다."""
    bus = EventBus()
    enrich_repo = _FakeEnrichQueueRepo()
    files = [{"repo_root": "/repo", "relative_path": "c.py", "content_hash": "h3"}]
    tool_repo = _FakeToolLayerRepo(files=files)
    w = _watcher(bus, enrich_repo=enrich_repo, tool_repo=tool_repo)
    w.start()

    # 먼저 활성화
    bus.publish(LspWarmReady(repo_root="/repo", language=Language.PYTHON))
    _wait(lambda: len(enrich_repo.enqueued) >= 1)
    enrich_repo.enqueued.clear()

    # 이후 L3FlushCompleted
    bus.publish(L3FlushCompleted(repo_root="/repo", flushed_count=1))
    _wait(lambda: len(enrich_repo.enqueued) >= 1)

    assert enrich_repo.enqueued[0]["relative_path"] == "c.py"
    bus.shutdown()


# ---------------------------------------------------------------------------
# EventBus.shutdown() → 루프 종료
# ---------------------------------------------------------------------------

def test_eventbus_shutdown_stops_watch_loop() -> None:
    """EventBus.shutdown() 시 watcher 스레드가 종료된다."""
    bus = EventBus()
    w = _watcher(bus, poll_interval_sec=100.0)
    w.start()
    assert w._thread is not None

    bus.shutdown()
    w._thread.join(timeout=2.0)
    assert not w._thread.is_alive()


# ---------------------------------------------------------------------------
# trigger_startup 테스트
# ---------------------------------------------------------------------------

def test_trigger_startup_activates_repo_when_stale_files_exist() -> None:
    """stale 파일이 있으면 trigger_startup()이 repo를 즉시 활성화한다."""
    bus = EventBus()
    enrich_repo = _FakeEnrichQueueRepo()
    files = [{"repo_root": "/repo", "relative_path": "d.py", "content_hash": "h4"}]
    tool_repo = _FakeToolLayerRepo(files=files, stale_count=1)
    w = _watcher(bus, enrich_repo=enrich_repo, tool_repo=tool_repo)
    w.start()

    w.trigger_startup(repo_root="/repo")

    _wait(lambda: len(enrich_repo.enqueued) >= 1)
    assert enrich_repo.enqueued[0]["relative_path"] == "d.py"

    bus.shutdown()


def test_trigger_startup_does_nothing_when_no_stale_files() -> None:
    """stale 파일이 없으면 trigger_startup()이 활성화하지 않는다."""
    bus = EventBus()
    enrich_repo = _FakeEnrichQueueRepo()
    tool_repo = _FakeToolLayerRepo(files=[], stale_count=0)
    w = _watcher(bus, enrich_repo=enrich_repo, tool_repo=tool_repo, poll_interval_sec=100.0)
    w.start()

    w.trigger_startup(repo_root="/repo")
    time.sleep(0.1)

    assert len(enrich_repo.enqueued) == 0
    bus.shutdown()


# ---------------------------------------------------------------------------
# process_batch 예외 처리
# ---------------------------------------------------------------------------

def test_process_batch_handles_query_exception_gracefully() -> None:
    """DB 조회에서 예외가 발생해도 watcher가 계속 동작한다."""
    bus = EventBus()
    enrich_repo = _FakeEnrichQueueRepo()

    class _BrokenRepo(_FakeToolLayerRepo):
        def list_l5_upgrade_candidates(self, **kwargs) -> list[dict]:
            raise RuntimeError("DB 연결 실패")

    w = _watcher(bus, enrich_repo=enrich_repo, tool_repo=_BrokenRepo())
    w.start()

    # 활성화 후 L3Flush → _process_batch 내 예외 발생
    bus.publish(LspWarmReady(repo_root="/repo", language=Language.PYTHON))
    time.sleep(0.1)

    # 스레드가 살아있어야 함
    assert w._thread is not None
    assert w._thread.is_alive()

    bus.shutdown()


def test_process_batch_no_files_does_not_enqueue() -> None:
    """DB 조회 결과가 없으면 enqueue가 호출되지 않는다."""
    bus = EventBus()
    enrich_repo = _FakeEnrichQueueRepo()
    tool_repo = _FakeToolLayerRepo(files=[])
    w = _watcher(bus, enrich_repo=enrich_repo, tool_repo=tool_repo)
    w.start()

    bus.publish(LspWarmReady(repo_root="/repo", language=Language.PYTHON))
    time.sleep(0.1)

    assert len(enrich_repo.enqueued) == 0
    bus.shutdown()


# ---------------------------------------------------------------------------
# batch_size 제한
# ---------------------------------------------------------------------------

def test_process_batch_respects_batch_size() -> None:
    """batch_size 이상의 파일은 조회하지 않는다."""
    bus = EventBus()
    enrich_repo = _FakeEnrichQueueRepo()
    files = [
        {"repo_root": "/r", "relative_path": f"f{i}.py", "content_hash": f"h{i}"}
        for i in range(10)
    ]
    tool_repo = _FakeToolLayerRepo(files=files)
    w = L5AsyncUpgradeWatcher(
        event_bus=bus,
        enrich_queue_repo=enrich_repo,
        tool_layer_repo=tool_repo,
        workspace_id="",
        batch_size=3,
        poll_interval_sec=100.0,
        enabled=True,
    )
    w.start()

    bus.publish(LspWarmReady(repo_root="/r", language=Language.PYTHON))
    _wait(lambda: len(enrich_repo.enqueued) >= 1)

    assert len(enrich_repo.enqueued) == 3

    bus.shutdown()
