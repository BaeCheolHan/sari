"""CLI 파이프라인 성능 명령을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
from pytest import MonkeyPatch

from sari.cli.main import _build_services, cli
from sari.services.file_collection_service import SolidLspExtractionBackend
from sari.services.pipeline_benchmark_service import BenchmarkLspExtractionBackend


def _prepare_home(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """CLI 기본 설정 경로가 임시 디렉터리를 사용하도록 HOME을 설정한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".local" / "share" / "sari-v2").mkdir(parents=True, exist_ok=True)


def test_cli_pipeline_perf_run_and_report(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline perf run/report 명령이 정상 동작해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()

    add_result = runner.invoke(cli, ["roots", "add", str(repo_dir)])
    assert add_result.exit_code == 0

    run_result = runner.invoke(
        cli,
        [
            "pipeline",
            "perf",
            "run",
            "--repo",
            str(repo_dir.resolve()),
            "--target-files",
            "2000",
            "--profile",
            "realistic_v1",
            "--dataset-mode",
            "isolated",
            "--fresh-db",
            "--reset-probe-state",
            "--cold-lsp-reset",
        ],
    )
    assert run_result.exit_code == 0
    run_payload = json.loads(run_result.output)
    assert run_payload["perf"]["status"] == "COMPLETED"
    assert run_payload["perf"]["threshold_profile"] == "realistic_v1"
    assert run_payload["perf"]["dataset_mode"] == "isolated"
    workspace = next(item for item in run_payload["perf"]["datasets"] if item["dataset_type"] == "workspace_real")
    assert workspace["run_context"]["fresh_db"] is True
    assert workspace["run_context"]["pre_state_reset"] is True
    assert workspace["run_context"]["cold_lsp_reset"] is True

    report_result = runner.invoke(
        cli,
        [
            "pipeline",
            "perf",
            "report",
            "--repo",
            str(repo_dir.resolve()),
        ],
    )
    assert report_result.exit_code == 0
    report_payload = json.loads(report_result.output)
    assert report_payload["perf"]["status"] == "COMPLETED"


def test_build_services_separates_benchmark_and_perf_backends(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """perf 측정 서비스는 real LSP backend를 사용해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    services = _build_services()
    benchmark_backend = services.pipeline_benchmark_service._file_collection_service._lsp_backend  # type: ignore[attr-defined]
    perf_backend = services.pipeline_perf_service._file_collection_service._lsp_backend  # type: ignore[attr-defined]

    assert isinstance(benchmark_backend, BenchmarkLspExtractionBackend)
    assert isinstance(perf_backend, SolidLspExtractionBackend)


def test_cli_pipeline_perf_run_passes_workspace_exclude_globs(monkeypatch: MonkeyPatch) -> None:
    """workspace-exclude-glob 옵션이 perf service.run()으로 전달되어야 한다."""
    runner = CliRunner()
    captured: dict[str, object] = {}

    class _FakePerfService:
        def run(self, **kwargs):  # noqa: ANN003, ANN201
            captured.update(kwargs)
            return {
                "status": "COMPLETED",
                "threshold_profile": kwargs["profile"],
                "dataset_mode": kwargs["dataset_mode"],
                "datasets": [
                    {"dataset_type": "workspace_real", "run_context": {"config_snapshot": {"workspace_exclude_globs": list(kwargs.get("workspace_exclude_globs", ()))}}}
                ],
            }

    monkeypatch.setattr(
        "sari.cli.main._build_services",
        lambda: SimpleNamespace(pipeline_perf_service=_FakePerfService()),
    )
    result = runner.invoke(
        cli,
        [
            "pipeline", "perf", "run",
            "--repo", "/tmp/repo",
            "--workspace-exclude-glob", "serena/test/resources/repos/**",
            "--workspace-exclude-glob", "**/benchmark_dataset/**",
        ],
    )
    assert result.exit_code == 0
    assert captured["workspace_exclude_globs"] == ("serena/test/resources/repos/**", "**/benchmark_dataset/**")


def test_cli_lsp_reset_unavailable_calls_perf_file_collection_service(monkeypatch: MonkeyPatch) -> None:
    """lsp reset-unavailable 명령이 perf file_collection_service의 reset API를 호출해야 한다."""
    runner = CliRunner()
    called: dict[str, object] = {}

    class _FakeCollectionService:
        def reset_lsp_unavailable_cache(self, repo_root=None, language=None):  # noqa: ANN001, ANN201
            called["repo_root"] = repo_root
            called["language"] = language
            return 7

    bundle = SimpleNamespace(
        pipeline_perf_service=SimpleNamespace(_file_collection_service=_FakeCollectionService()),
    )
    monkeypatch.setattr("sari.cli.main._build_services", lambda: bundle)

    result = runner.invoke(cli, ["lsp", "reset-unavailable", "--repo", "/tmp/repo", "--lang", "java"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["lsp_unavailable_reset"]["cleared_count"] == 7
    assert called["language"] == "java"
