"""release_gate_mcp_probe의 call_flow 모드를 검증한다."""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
import sys


def _load_probe_module():
    """tools/ci/release_gate_mcp_probe.py 모듈을 로드한다."""
    root = Path(__file__).resolve().parents[2]
    probe_path = root / "tools" / "ci" / "release_gate_mcp_probe.py"
    spec = importlib.util.spec_from_file_location("release_gate_mcp_probe", probe_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_call_flow_success(monkeypatch):
    """call_flow 모드는 내부 클라이언트 성공 시 0을 반환해야 한다."""
    probe = _load_probe_module()

    def fake_run_internal_client(**kwargs):
        assert kwargs["run_call_flow"] is True
        assert kwargs["repo"] == "/tmp/repo"
        return True, {"stage": "ok", "tool_count": 1}

    captured: dict[str, object] = {}

    def fake_emit_summary(mode: str, ok: bool, detail: dict[str, object]) -> None:
        captured["mode"] = mode
        captured["ok"] = ok
        captured["detail"] = detail

    monkeypatch.setenv("SARI_MCP_PROBE_REPO", "/tmp/repo")
    monkeypatch.setattr(probe, "_ensure_probe_repo_registered", lambda _: None)
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda *args, **kwargs: type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})(),
    )
    monkeypatch.setattr(probe, "_run_internal_client", fake_run_internal_client)
    monkeypatch.setattr(probe, "_emit_summary", fake_emit_summary)

    assert probe._run_call_flow() == 0
    assert captured["mode"] == "call_flow"
    assert captured["ok"] is True


def test_main_routes_call_flow(monkeypatch):
    """main은 call_flow 인자일 때 run_call_flow를 호출해야 한다."""
    probe = _load_probe_module()
    monkeypatch.setattr(sys, "argv", ["release_gate_mcp_probe.py", "call_flow"])
    monkeypatch.setattr(probe.subprocess, "run", lambda *args, **kwargs: None)

    called: dict[str, bool] = {"call_flow": False}

    def fake_run_call_flow() -> int:
        called["call_flow"] = True
        return 0

    monkeypatch.setattr(probe, "_run_call_flow", fake_run_call_flow)
    monkeypatch.setattr(probe, "_run_handshake", lambda: 0)
    monkeypatch.setattr(probe, "_run_concurrency", lambda: 0)

    assert probe.main() == 0
    assert called["call_flow"] is True


def test_run_soak_passes_under_threshold(monkeypatch):
    """soak은 허용 실패율/타임아웃 상한 이내일 때 성공해야 한다."""
    probe = _load_probe_module()
    monkeypatch.setenv("SARI_MCP_PROBE_REPO", "/tmp/repo")
    monkeypatch.setenv("SARI_MCP_SOAK_DURATION_SEC", "1")
    monkeypatch.setenv("SARI_MCP_SOAK_INTERVAL_SEC", "0.1")
    monkeypatch.setenv("SARI_MCP_SOAK_MAX_FAILURE_RATE", "0.6")
    monkeypatch.setenv("SARI_MCP_SOAK_MAX_TIMEOUT_FAILURES", "1")
    monkeypatch.setenv("SARI_MCP_SOAK_MIN_ATTEMPTS", "2")
    monkeypatch.setattr(probe, "_ensure_probe_repo_registered", lambda _: None)
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda *args, **kwargs: type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})(),
    )
    monkeypatch.setattr(probe.time, "sleep", lambda _x: None)

    time_values = iter([0.0, 0.2, 1.2])
    monkeypatch.setattr(probe.time, "time", lambda: next(time_values))
    outcomes = [True, False]
    calls: list[dict[str, object]] = []
    lock = threading.Lock()

    def _run_internal_client(**kwargs):
        calls.append(kwargs)
        with lock:
            ok = outcomes.pop(0) if len(outcomes) > 0 else True
        if ok:
            return True, {"stage": "ok"}
        return False, {"stage": "timeout", "reason": "timeout"}

    captured: dict[str, object] = {}
    monkeypatch.setattr(probe, "_run_internal_client", _run_internal_client)
    monkeypatch.setattr(probe, "_emit_summary", lambda mode, ok, detail: captured.update({"mode": mode, "ok": ok, "detail": detail}))

    assert probe._run_soak() == 0
    assert captured["mode"] == "soak"
    assert captured["ok"] is True
    assert len(calls) > 0
    assert all(call.get("use_local_server") is False for call in calls)


def test_run_soak_fails_when_timeout_failures_exceed_limit(monkeypatch):
    """soak은 타임아웃 실패 상한을 초과하면 실패해야 한다."""
    probe = _load_probe_module()
    monkeypatch.setenv("SARI_MCP_PROBE_REPO", "/tmp/repo")
    monkeypatch.setenv("SARI_MCP_SOAK_DURATION_SEC", "1")
    monkeypatch.setenv("SARI_MCP_SOAK_INTERVAL_SEC", "0.1")
    monkeypatch.setenv("SARI_MCP_SOAK_MAX_FAILURE_RATE", "1.0")
    monkeypatch.setenv("SARI_MCP_SOAK_MAX_TIMEOUT_FAILURES", "0")
    monkeypatch.setenv("SARI_MCP_SOAK_MIN_ATTEMPTS", "2")
    monkeypatch.setattr(probe, "_ensure_probe_repo_registered", lambda _: None)
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda *args, **kwargs: type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})(),
    )
    monkeypatch.setattr(probe.time, "sleep", lambda _x: None)

    time_values = iter([0.0, 0.2, 1.2])
    monkeypatch.setattr(probe.time, "time", lambda: next(time_values))
    monkeypatch.setattr(probe, "_run_internal_client", lambda **kwargs: (False, {"stage": "timeout", "reason": "timeout"}))

    try:
        _ = probe._run_soak()
    except RuntimeError as exc:
        message = str(exc)
        assert "mcp soak failed" in message
        assert "timeout_failures" in message
    else:
        raise AssertionError("RuntimeError must be raised when timeout failures exceed limit")
