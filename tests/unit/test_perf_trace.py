"""Perf tracer 동작을 검증한다."""

from __future__ import annotations

import logging

from sari.services.collection.perf_trace import PerfTracer, trace_methods


def test_perf_tracer_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SARI_PERF_TRACE", raising=False)
    tracer = PerfTracer(component="test")
    assert tracer.enabled is False


def test_perf_tracer_sampling_interval(monkeypatch) -> None:
    monkeypatch.setenv("SARI_PERF_TRACE", "1")
    monkeypatch.setenv("SARI_PERF_TRACE_EVERY", "3")
    tracer = PerfTracer(component="test")

    assert tracer.should_sample() is False
    assert tracer.should_sample() is False
    assert tracer.should_sample() is True


def test_perf_tracer_emits_structured_log_when_enabled(monkeypatch, caplog) -> None:
    monkeypatch.setenv("SARI_PERF_TRACE", "1")
    tracer = PerfTracer(component="test")

    with caplog.at_level(logging.INFO):
        tracer.emit("batch_done", processed=5)

    assert any("sari_perf_trace" in message and "batch_done" in message for message in caplog.messages)


def test_trace_methods_decorator_emits_start_and_end(monkeypatch, caplog) -> None:
    monkeypatch.setenv("SARI_PERF_TRACE", "1")

    @trace_methods("decorated_test")
    class _Demo:
        def run(self, value: int) -> int:
            return value + 1

    with caplog.at_level(logging.INFO):
        result = _Demo().run(1)

    assert result == 2
    assert any('"event": "fn_start"' in message for message in caplog.messages)
    assert any('"event": "fn_end"' in message for message in caplog.messages)
