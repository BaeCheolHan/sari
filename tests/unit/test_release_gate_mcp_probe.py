"""release_gate_mcp_probe의 call_flow 모드를 검증한다."""

from __future__ import annotations

import importlib.util
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
