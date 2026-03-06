"""데몬 프로세스의 AdminService 주입 구성을 검증한다."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import cast
from types import SimpleNamespace

from pytest import MonkeyPatch
from starlette.applications import Starlette

from sari import daemon_process
from sari.core.config import AppConfig


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

        def reconcile_runtime(self) -> int:
            """테스트 reconcile 호출을 흉내낸다."""
            return 0

        def get_metrics(self) -> dict[str, int]:
            """테스트 메트릭 스냅샷을 반환한다."""
            return {"lsp_instance_count": 0}

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


def test_main_wires_repo_probe_repository_into_lsp_backend(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    captured: dict[str, object] = {}

    class _FakeBackend:
        def __init__(self, hub: object, **kwargs: object) -> None:
            captured["hub"] = hub
            captured["repo_language_probe_repo"] = kwargs.get("repo_language_probe_repo")

    class _FakeFileCollectionService:
        def set_l5_admission_mode(self, enabled: bool) -> None:
            del enabled

        def get_pipeline_metrics(self) -> dict[str, object]:
            return {}

        def start_background(self) -> None:
            return

        def stop_background(self) -> None:
            return

    def _fake_parse_args() -> Namespace:
        return Namespace(db_path=str(db_path), host="127.0.0.1", port=40125, run_mode="dev")

    def _fake_build_search_stack(**kwargs):
        del kwargs
        return SimpleNamespace(candidate_service=object(), vector_sink=object(), orchestrator=object())

    def _fake_build_file_collection_service_from_config(**kwargs):
        captured["lsp_backend"] = kwargs["lsp_backend"]
        return _FakeFileCollectionService()

    def _fake_create_app(context):
        return SimpleNamespace(state=SimpleNamespace(context=context))

    monkeypatch.setattr(daemon_process, "parse_args", _fake_parse_args)
    monkeypatch.setattr(daemon_process, "build_search_stack", _fake_build_search_stack)
    monkeypatch.setattr(daemon_process, "build_file_collection_service_from_config", _fake_build_file_collection_service_from_config)
    monkeypatch.setattr(daemon_process, "SolidLspExtractionBackend", _FakeBackend)
    monkeypatch.setattr(daemon_process, "create_app", _fake_create_app)
    monkeypatch.setattr(daemon_process.uvicorn, "run", lambda *args, **kwargs: None)

    daemon_process.main()

    assert captured["repo_language_probe_repo"] is not None


def test_is_parent_alive_treats_detached_ppid_as_alive(monkeypatch: MonkeyPatch) -> None:
    """ppid=1(detached) 환경은 orphan으로 간주하지 않아야 한다."""
    monkeypatch.setattr(daemon_process.os, "getppid", lambda: 1)
    assert daemon_process._is_parent_alive() is True


def test_should_orphan_terminate_requires_confirm_window() -> None:
    """orphan 판정은 confirm_probes 횟수만큼 연속 실패해야 종료되어야 한다."""
    terminate, miss = daemon_process._should_orphan_terminate(
        parent_alive=False,
        detached_mode=False,
        miss_count=0,
        confirm_probes=3,
    )
    assert terminate is False
    assert miss == 1
    terminate, miss = daemon_process._should_orphan_terminate(
        parent_alive=False,
        detached_mode=False,
        miss_count=miss,
        confirm_probes=3,
    )
    assert terminate is False
    assert miss == 2
    terminate, miss = daemon_process._should_orphan_terminate(
        parent_alive=False,
        detached_mode=False,
        miss_count=miss,
        confirm_probes=3,
    )
    assert terminate is True
    assert miss == 3


def test_should_orphan_terminate_resets_on_parent_recovery() -> None:
    """부모가 다시 살아나면 orphan miss 카운트는 초기화되어야 한다."""
    terminate, miss = daemon_process._should_orphan_terminate(
        parent_alive=True,
        detached_mode=False,
        miss_count=2,
        confirm_probes=3,
    )
    assert terminate is False
    assert miss == 0


def test_should_run_periodic_reconcile_interval_gate() -> None:
    should_run = daemon_process._should_run_periodic_reconcile(
        now_monotonic=10.0,
        last_run_monotonic=0.0,
        interval_sec=30.0,
        inflight=False,
    )
    assert should_run is False

    should_run = daemon_process._should_run_periodic_reconcile(
        now_monotonic=31.0,
        last_run_monotonic=0.0,
        interval_sec=30.0,
        inflight=False,
    )
    assert should_run is True


def test_should_run_periodic_reconcile_skips_when_inflight() -> None:
    should_run = daemon_process._should_run_periodic_reconcile(
        now_monotonic=60.0,
        last_run_monotonic=0.0,
        interval_sec=10.0,
        inflight=True,
    )
    assert should_run is False


def test_should_run_auto_control_interval_gate() -> None:
    should_run = daemon_process._should_run_auto_control(
        now_monotonic=1.0,
        last_run_monotonic=0.0,
        interval_sec=5.0,
    )
    assert should_run is False

    should_run = daemon_process._should_run_auto_control(
        now_monotonic=5.0,
        last_run_monotonic=0.0,
        interval_sec=5.0,
    )
    assert should_run is True


def test_should_run_auto_control_handles_non_positive_time() -> None:
    should_run = daemon_process._should_run_auto_control(
        now_monotonic=-1.0,
        last_run_monotonic=0.0,
        interval_sec=5.0,
    )
    assert should_run is False

def test_build_daemon_config_overlays_cli_on_loaded_defaults(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    loaded = AppConfig(
        db_path=tmp_path / "loaded.db",
        host="0.0.0.0",
        preferred_port=49999,
        max_port_scan=5,
        stop_grace_sec=3,
        run_mode="prod",
        daemon_reconcile_interval_sec=77,
    )
    monkeypatch.setattr(daemon_process.AppConfig, "default", classmethod(lambda cls: loaded))

    config = daemon_process._build_daemon_config(
        db_path=tmp_path / "state.db",
        host="127.0.0.1",
        port=40123,
        run_mode="dev",
    )

    assert config.db_path == (tmp_path / "state.db")
    assert config.host == "127.0.0.1"
    assert config.preferred_port == 40123
    assert config.max_port_scan == 50
    assert config.stop_grace_sec == 10
    assert config.run_mode == "dev"
    # env/file loaded 값은 유지되어야 한다.
    assert config.daemon_reconcile_interval_sec == 77
