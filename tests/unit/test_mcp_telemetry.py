import io
import sys
import types
import time
from pathlib import Path

import mcp.telemetry as telemetry_mod


def test_logger_no_dir_no_queue(monkeypatch):
    logger = telemetry_mod.TelemetryLogger(None)
    assert logger.get_queue_depth() == 0
    assert logger.get_drop_count() == 0

    logger.log_info("x")
    logger.log_error("y")
    logger.log_telemetry("z")
    logger.stop()


def test_logger_queue_and_drop(monkeypatch, tmp_path):
    logger = telemetry_mod.TelemetryLogger(tmp_path)
    logger._backlog_limit = 1
    logger._queue = types.SimpleNamespace(
        qsize=lambda: 2,
        empty=lambda: True,
        put=lambda _m: None,
    )
    logger._stop_event.set()
    if logger._writer_thread:
        logger._writer_thread.join(timeout=0.2)
    logger.log_telemetry("a")
    logger.log_telemetry("b")
    assert logger.get_drop_count() >= 1
    logger.stop()


def test_logger_enqueue_no_queue():
    logger = telemetry_mod.TelemetryLogger(None)
    logger._enqueue("x")


def test_writer_loop_empty_queue(monkeypatch, tmp_path):
    logger = telemetry_mod.TelemetryLogger(tmp_path)
    logger._stop_event.set()
    logger._writer_loop()


def test_write_to_file_success(monkeypatch, tmp_path):
    logger = telemetry_mod.TelemetryLogger(tmp_path)
    logger._write_to_file("hello")
    log_file = tmp_path / "deckard.log"
    assert log_file.exists()
    data = log_file.read_text(encoding="utf-8")
    assert "hello" in data
    logger.stop()


def test_write_to_file_redact_and_fail(monkeypatch, tmp_path):
    def fake_redact(msg):
        return "REDACTED"
    monkeypatch.setattr(telemetry_mod, "_redact", fake_redact)

    logger = telemetry_mod.TelemetryLogger(tmp_path)

    def bad_open(*_args, **_kwargs):
        raise RuntimeError("boom")

    import builtins
    monkeypatch.setattr(builtins, "open", bad_open)
    logger._write_to_file("secret")
    logger.stop()


def test_log_error_info_prints(monkeypatch, tmp_path):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    logger = telemetry_mod.TelemetryLogger(None)
    logger.log_error("err")
    logger.log_info("info")
    out = buf.getvalue()
    assert "ERROR" in out
    assert "INFO" in out


def test_stop_without_queue():
    logger = telemetry_mod.TelemetryLogger(None)
    logger.stop()
