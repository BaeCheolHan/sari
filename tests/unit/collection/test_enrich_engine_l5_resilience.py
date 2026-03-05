from __future__ import annotations

import concurrent.futures
from concurrent.futures import Future
from dataclasses import dataclass, field
from itertools import count
import threading
from unittest.mock import MagicMock

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.enrich_engine import EnrichEngine


class _FailingExecutor:
    def submit(self, fn, *args, **kwargs):  # noqa: ANN001, D401
        future: Future = Future()
        try:
            fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)
        else:
            future.set_result(None)
        return future


@dataclass
class _CapturedFlush:
    failed_job_ids: list[str] = field(default_factory=list)
    failed_messages: list[str] = field(default_factory=list)

    def flush(self, *, buffers, body_upserts):  # noqa: ANN001
        del body_upserts
        for update in buffers.failed_updates:
            self.failed_job_ids.append(update.job_id)
            self.failed_messages.append(update.error_message)
        buffers.failed_updates.clear()
        buffers.done_ids.clear()


class _QueueRepo:
    def __init__(self, jobs: list[FileEnrichJobDTO]) -> None:
        self._jobs = list(jobs)

    def acquire_pending_for_l5(self, *, limit: int, now_iso: str) -> list[FileEnrichJobDTO]:
        del limit, now_iso
        jobs = self._jobs
        self._jobs = []
        return jobs


class _ScriptedFuture:
    def __init__(
        self,
        *,
        result_obj: object,
        running_state: bool = False,
        cancel_result: bool = False,
        done_state: bool = False,
    ) -> None:
        self._result_obj = result_obj
        self._running_state = running_state
        self._cancel_result = cancel_result
        self._done_state = done_state
        self.cancel_called = 0

    def running(self) -> bool:
        return self._running_state

    def set_running(self, value: bool) -> None:
        self._running_state = value

    def done(self) -> bool:
        return self._done_state

    def set_done(self, value: bool) -> None:
        self._done_state = value

    def cancel(self) -> bool:
        self.cancel_called += 1
        return self._cancel_result

    def result(self) -> object:
        return self._result_obj


class _ScriptedExecutor:
    def __init__(self, future: object | list[object]) -> None:
        if isinstance(future, list):
            self._futures = list(future)
        else:
            self._futures = [future]

    def submit(self, fn, *args, **kwargs):  # noqa: ANN001, D401
        del fn, args, kwargs
        if not self._futures:
            raise RuntimeError("no scripted future left")
        return self._futures.pop(0)


def _sample_l5_job(job_id: str = "job-1") -> FileEnrichJobDTO:
    ts = "2026-03-04T00:00:00+00:00"
    return FileEnrichJobDTO(
        job_id=job_id,
        repo_id="repo",
        repo_root="/repo",
        relative_path="a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l5",
        status="PENDING",
        attempt_count=0,
        last_error=None,
        next_retry_at=ts,
        created_at=ts,
        updated_at=ts,
    )


def test_process_enrich_jobs_l5_converts_future_exception_to_failed_update() -> None:
    engine = object.__new__(EnrichEngine)
    engine._assert_parent_alive = lambda worker_name: None
    engine._l5_detached_futures = {}
    engine._l5_detached_lock = threading.Lock()
    engine._enrich_queue_repo = _QueueRepo([_sample_l5_job()])
    engine._rebalance_jobs_by_language = lambda jobs: jobs
    engine._l3_executor = _FailingExecutor()
    engine._process_single_l5_job = lambda job: (_ for _ in ()).throw(RuntimeError("boom"))
    engine._l3_result_merger = MagicMock()
    engine._flush_batch_size = 100
    engine._flush_interval_sec = 9999.0
    engine._policy = type("Policy", (), {"retry_max_attempts": 5, "retry_backoff_base_sec": 1})()

    captured = _CapturedFlush()
    engine._l3_flush_coordinator = captured

    processed = engine.process_enrich_jobs_l5(limit=10)

    assert processed == 1
    assert captured.failed_job_ids == ["job-1"]
    assert len(captured.failed_messages) == 1
    assert "L5 future failed" in captured.failed_messages[0]


def test_process_enrich_jobs_l5_detaches_running_future_when_cancel_returns_false(monkeypatch) -> None:  # noqa: ANN001
    engine = object.__new__(EnrichEngine)
    engine._assert_parent_alive = lambda worker_name: None
    engine._l5_detached_futures = {}
    engine._l5_detached_lock = threading.Lock()
    engine._enrich_queue_repo = _QueueRepo([_sample_l5_job()])
    engine._rebalance_jobs_by_language = lambda jobs: jobs
    future = _ScriptedFuture(result_obj=object(), running_state=True, cancel_result=False)
    engine._l3_executor = _ScriptedExecutor(future)
    engine._process_single_l5_job = lambda job: object()
    engine._l3_result_merger = MagicMock()
    engine._flush_batch_size = 100
    engine._flush_interval_sec = 9999.0
    engine._policy = type("Policy", (), {"retry_max_attempts": 5, "retry_backoff_base_sec": 1})()
    engine._l5_batch_wait_timeout_sec = 1.0
    captured = _CapturedFlush()
    engine._l3_flush_coordinator = captured

    call_count = {"value": 0}

    def _fake_wait(pending, timeout, return_when):  # noqa: ANN001
        del timeout, return_when
        call_count["value"] += 1
        only = next(iter(pending))
        if call_count["value"] <= 2:
            return set(), set(pending)
        return {only}, set()

    perf_ticks = count(start=10.0, step=2.0)
    monkeypatch.setattr(concurrent.futures, "wait", _fake_wait)
    monkeypatch.setattr("sari.services.collection.enrich_engine.time.perf_counter", lambda: next(perf_ticks))

    processed = engine.process_enrich_jobs_l5(limit=10)

    assert processed == 0
    assert captured.failed_job_ids == []
    assert future.cancel_called >= 1
    assert engine._l3_result_merger.merge.call_count == 0
    assert len(engine._l5_detached_futures) == 1


def test_process_enrich_jobs_l5_does_not_timeout_queued_future_before_running(monkeypatch) -> None:  # noqa: ANN001
    engine = object.__new__(EnrichEngine)
    engine._assert_parent_alive = lambda worker_name: None
    engine._l5_detached_futures = {}
    engine._l5_detached_lock = threading.Lock()
    engine._enrich_queue_repo = _QueueRepo([_sample_l5_job()])
    engine._rebalance_jobs_by_language = lambda jobs: jobs
    future = _ScriptedFuture(result_obj=object(), running_state=False, cancel_result=True)
    engine._l3_executor = _ScriptedExecutor(future)
    engine._process_single_l5_job = lambda job: object()
    engine._l3_result_merger = MagicMock()
    engine._flush_batch_size = 100
    engine._flush_interval_sec = 9999.0
    engine._policy = type("Policy", (), {"retry_max_attempts": 5, "retry_backoff_base_sec": 1})()
    engine._l5_batch_wait_timeout_sec = 1.0
    captured = _CapturedFlush()
    engine._l3_flush_coordinator = captured

    call_count = {"value": 0}

    def _fake_wait(pending, timeout, return_when):  # noqa: ANN001
        del timeout, return_when
        call_count["value"] += 1
        only = next(iter(pending))
        if call_count["value"] == 1:
            return set(), set(pending)
        return {only}, set()

    perf_ticks = count(start=10.0, step=2.0)
    monkeypatch.setattr(concurrent.futures, "wait", _fake_wait)
    monkeypatch.setattr("sari.services.collection.enrich_engine.time.perf_counter", lambda: next(perf_ticks))

    processed = engine.process_enrich_jobs_l5(limit=10)

    assert processed == 1
    assert captured.failed_job_ids == []
    assert future.cancel_called == 0
    assert engine._l3_result_merger.merge.call_count == 1


def test_process_enrich_jobs_l5_times_out_queued_future_when_running_future_is_stuck(monkeypatch) -> None:  # noqa: ANN001
    engine = object.__new__(EnrichEngine)
    first = _sample_l5_job("job-1")
    second = _sample_l5_job("job-2")
    engine._assert_parent_alive = lambda worker_name: None
    engine._l5_detached_futures = {}
    engine._l5_detached_lock = threading.Lock()
    engine._enrich_queue_repo = _QueueRepo([first, second])
    engine._rebalance_jobs_by_language = lambda jobs: jobs
    running_hung = _ScriptedFuture(result_obj=object(), running_state=True, cancel_result=False)
    queued = _ScriptedFuture(result_obj=object(), running_state=False, cancel_result=True)
    engine._l3_executor = _ScriptedExecutor([running_hung, queued])
    engine._process_single_l5_job = lambda job: object()
    engine._l3_result_merger = MagicMock()
    engine._flush_batch_size = 100
    engine._flush_interval_sec = 9999.0
    engine._policy = type("Policy", (), {"retry_max_attempts": 5, "retry_backoff_base_sec": 1})()
    engine._l5_batch_wait_timeout_sec = 1.0
    captured = _CapturedFlush()
    engine._l3_flush_coordinator = captured

    def _fake_wait(pending, timeout, return_when):  # noqa: ANN001
        del timeout, return_when
        return set(), set(pending)

    perf_ticks = count(start=10.0, step=2.0)
    monkeypatch.setattr(concurrent.futures, "wait", _fake_wait)
    monkeypatch.setattr("sari.services.collection.enrich_engine.time.perf_counter", lambda: next(perf_ticks))

    processed = engine.process_enrich_jobs_l5(limit=10)

    assert processed == 1
    assert sorted(captured.failed_job_ids) == ["job-2"]
    assert any("cancelled_queued" in message for message in captured.failed_messages)
    assert engine._l3_result_merger.merge.call_count == 0
    assert len(engine._l5_detached_futures) == 1


def test_process_enrich_jobs_l5_collects_detached_completion_next_cycle(monkeypatch) -> None:  # noqa: ANN001
    engine = object.__new__(EnrichEngine)
    engine._assert_parent_alive = lambda worker_name: None
    done_future = _ScriptedFuture(result_obj=object(), done_state=True)
    engine._l5_detached_futures = {done_future: _sample_l5_job("job-detached")}
    engine._l5_detached_lock = threading.Lock()
    engine._enrich_queue_repo = _QueueRepo([])
    engine._rebalance_jobs_by_language = lambda jobs: jobs
    engine._l3_executor = _ScriptedExecutor([])
    engine._process_single_l5_job = lambda job: object()
    engine._l3_result_merger = MagicMock()
    engine._flush_batch_size = 100
    engine._flush_interval_sec = 9999.0
    engine._policy = type("Policy", (), {"retry_max_attempts": 5, "retry_backoff_base_sec": 1})()
    engine._l5_batch_wait_timeout_sec = 1.0
    captured = _CapturedFlush()
    engine._l3_flush_coordinator = captured
    monkeypatch.setattr("sari.services.collection.enrich_engine.time.perf_counter", lambda: 1.0)

    processed = engine.process_enrich_jobs_l5(limit=10)

    assert processed == 1
    assert engine._l3_result_merger.merge.call_count == 1
    assert len(engine._l5_detached_futures) == 0


def test_process_enrich_jobs_l5_does_not_count_pending_only_result_as_processed() -> None:
    engine = object.__new__(EnrichEngine)
    engine._assert_parent_alive = lambda worker_name: None
    engine._l5_detached_futures = {}
    engine._l5_detached_lock = threading.Lock()
    engine._enrich_queue_repo = _QueueRepo([_sample_l5_job("job-pending-only")])
    engine._rebalance_jobs_by_language = lambda jobs: jobs
    pending_only_result = type("PendingOnlyResult", (), {"done_id": None, "failure_update": None})()
    future: Future = Future()
    future.set_result(pending_only_result)
    engine._l3_executor = _ScriptedExecutor(future)
    engine._process_single_l5_job = lambda job: pending_only_result
    engine._l3_result_merger = MagicMock()
    engine._flush_batch_size = 100
    engine._flush_interval_sec = 9999.0
    engine._policy = type("Policy", (), {"retry_max_attempts": 5, "retry_backoff_base_sec": 1})()
    engine._l5_batch_wait_timeout_sec = 1.0
    captured = _CapturedFlush()
    engine._l3_flush_coordinator = captured

    processed = engine.process_enrich_jobs_l5(limit=10)

    assert processed == 0
    assert engine._l3_result_merger.merge.call_count == 1
    assert captured.failed_job_ids == []


def test_process_enrich_jobs_l5_times_out_queued_when_detached_backlog_exists(monkeypatch) -> None:  # noqa: ANN001
    engine = object.__new__(EnrichEngine)
    engine._assert_parent_alive = lambda worker_name: None
    active_detached = _ScriptedFuture(result_obj=object(), done_state=False)
    engine._l5_detached_futures = {active_detached: _sample_l5_job("job-old")}
    engine._l5_detached_lock = threading.Lock()
    queued_job = _sample_l5_job("job-queued")
    engine._enrich_queue_repo = _QueueRepo([queued_job])
    engine._rebalance_jobs_by_language = lambda jobs: jobs
    queued_future = _ScriptedFuture(result_obj=object(), running_state=False, cancel_result=True)
    engine._l3_executor = _ScriptedExecutor(queued_future)
    engine._process_single_l5_job = lambda job: object()
    engine._l3_result_merger = MagicMock()
    engine._flush_batch_size = 100
    engine._flush_interval_sec = 9999.0
    engine._policy = type("Policy", (), {"retry_max_attempts": 5, "retry_backoff_base_sec": 1})()
    engine._l5_batch_wait_timeout_sec = 1.0
    captured = _CapturedFlush()
    engine._l3_flush_coordinator = captured

    def _fake_wait(pending, timeout, return_when):  # noqa: ANN001
        del timeout, return_when
        return set(), set(pending)

    perf_ticks = count(start=10.0, step=2.0)
    monkeypatch.setattr(concurrent.futures, "wait", _fake_wait)
    monkeypatch.setattr("sari.services.collection.enrich_engine.time.perf_counter", lambda: next(perf_ticks))

    processed = engine.process_enrich_jobs_l5(limit=10)

    assert processed == 1
    assert captured.failed_job_ids == ["job-queued"]
    assert "cancelled_queued" in captured.failed_messages[0]
