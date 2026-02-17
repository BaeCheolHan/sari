"""CLI 파이프라인 벤치마크 명령을 검증한다."""

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


def test_cli_pipeline_benchmark_run_and_report(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline benchmark run/report 명령이 정상 동작해야 한다."""
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
            "benchmark",
            "run",
            "--repo",
            str(repo_dir.resolve()),
            "--target-files",
            "30",
            "--profile",
            "default",
            "--language-filter",
            "python",
            "--per-language-report",
        ],
    )
    assert run_result.exit_code == 0
    run_payload = json.loads(run_result.output)
    assert run_payload["benchmark"]["status"] == "COMPLETED"
    assert run_payload["benchmark"]["target_files"] == 30
    assert run_payload["benchmark"]["language_filter"] == ["python"]
    assert run_payload["benchmark"]["per_language_report"] is True

    report_result = runner.invoke(cli, ["pipeline", "benchmark", "report", "--latest"])
    assert report_result.exit_code == 0
    report_payload = json.loads(report_result.output)
    assert "benchmark" in report_payload
    assert report_payload["benchmark"]["status"] == "COMPLETED"
