"""Perf tracer 동작을 검증한다."""

from __future__ import annotations

import logging

from sari.services.collection.perf_trace import (
    PerfTracer,
    get_perf_trace_summary,
    perf_trace_session,
    reset_perf_trace_summary,
    trace_methods,
)


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


def test_perf_tracer_span_records_summary(monkeypatch, caplog) -> None:
    monkeypatch.setenv("SARI_PERF_TRACE", "1")
    session_id = "test-span-summary"
    reset_perf_trace_summary(session_id)
    tracer = PerfTracer(component="perf_test")

    with caplog.at_level(logging.INFO):
        with perf_trace_session(session_id):
            with tracer.span("demo_span", phase="unit", language="python"):
                pass

    summary = get_perf_trace_summary(session_id)
    assert summary["session_id"] == session_id
    groups = summary["span_groups"]
    assert isinstance(groups, list)
    assert len(groups) >= 1
    first = groups[0]
    assert first["component"] == "perf_test"
    assert first["event"] == "demo_span"
    assert first["phase"] == "unit"
    assert any('"event": "span"' in message for message in caplog.messages)
