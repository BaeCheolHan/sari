"""데몬 프로세스의 AdminService 주입 구성을 검증한다."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import cast

from pytest import MonkeyPatch
from starlette.applications import Starlette

from sari import daemon_process


def test_main_wires_admin_service_and_runs_uvicorn(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """main이 AdminService를 포함한 HttpContext를 구성하고 uvicorn을 호출해야 한다."""
    db_path = tmp_path / "state.db"
    captured: dict[str, object] = {}

    def _fake_parse_args() -> Namespace:
        return Namespace(db_path=str(db_path), host="127.0.0.1", port=40123, run_mode="dev")

    def _fake_run(app: Starlette, host: str, port: int, log_level: str) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["log_level"] = log_level

    monkeypatch.setattr(daemon_process, "parse_args", _fake_parse_args)
    monkeypatch.setattr(daemon_process.uvicorn, "run", _fake_run)

    daemon_process.main()

    app = cast(Starlette, captured["app"])
    context = app.state.context
    assert hasattr(context, "admin_service")
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 40123
    assert captured["log_level"] == "error"


def test_main_stops_lsp_hub_on_shutdown(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """main 종료 시 LSP Hub의 stop_all이 호출되어야 한다."""
    db_path = tmp_path / "state.db"
    captured: dict[str, object] = {"stop_all_called": False}

    class _FakeHub:
        """테스트용 LSP Hub 대체 객체."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            """기본 생성자."""
            del args, kwargs
            return

        def stop_all(self) -> None:
            """종료 호출 여부를 기록한다."""
            captured["stop_all_called"] = True

    def _fake_parse_args() -> Namespace:
        return Namespace(db_path=str(db_path), host="127.0.0.1", port=40124, run_mode="dev")

    def _fake_run(app: Starlette, host: str, port: int, log_level: str) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["log_level"] = log_level

    monkeypatch.setattr(daemon_process, "LspHub", _FakeHub)
    monkeypatch.setattr(daemon_process, "parse_args", _fake_parse_args)
    monkeypatch.setattr(daemon_process.uvicorn, "run", _fake_run)

    daemon_process.main()

    assert captured["stop_all_called"] is True
