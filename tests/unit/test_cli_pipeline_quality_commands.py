"""CLI 파이프라인 품질 명령을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from pytest import MonkeyPatch

from sari.cli.main import cli


def _prepare_home(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """CLI 기본 설정 경로가 임시 디렉터리를 사용하도록 HOME을 설정한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".local" / "share" / "sari-v2").mkdir(parents=True, exist_ok=True)


def test_cli_pipeline_quality_run_and_report(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline quality run/report 명령이 정상 동작해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    add_result = runner.invoke(cli, ["roots", "add", str(repo_dir)])
    assert add_result.exit_code == 0

    benchmark_result = runner.invoke(
        cli,
        ["pipeline", "benchmark", "run", "--repo", str(repo_dir.resolve()), "--target-files", "10", "--profile", "default"],
    )
    assert benchmark_result.exit_code == 0

    run_result = runner.invoke(
        cli,
        [
            "pipeline",
            "quality",
            "run",
            "--repo",
            str(repo_dir.resolve()),
            "--limit-files",
            "100",
            "--profile",
            "default",
            "--language-filter",
            "python",
        ],
    )
    assert run_result.exit_code == 0
    run_payload = json.loads(run_result.output)
    assert run_payload["quality"]["status"] in {"PASSED", "FAILED"}
    assert run_payload["quality"]["language_filter"] == ["python"]

    report_result = runner.invoke(cli, ["pipeline", "quality", "report", "--repo", str(repo_dir.resolve())])
    assert report_result.exit_code == 0
    report_payload = json.loads(report_result.output)
    assert "quality" in report_payload
