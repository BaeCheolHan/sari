"""CLI에서 pipeline benchmark 그룹 제거를 검증한다."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from pytest import MonkeyPatch

from sari.cli.main import cli


def _prepare_home(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".local" / "share" / "sari-v2").mkdir(parents=True, exist_ok=True)


def test_cli_pipeline_benchmark_group_is_removed(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline benchmark 명령 그룹이 더 이상 노출되지 않아야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["pipeline", "benchmark"])

    assert result.exit_code == 2
    assert "No such command 'benchmark'" in result.output
