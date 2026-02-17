"""CLI LSP 언어 매트릭스 명령을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
from pytest import MonkeyPatch

from sari.cli.main import cli


def _prepare_home(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """CLI 홈 경로를 임시 디렉터리로 고정한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".local" / "share" / "sari-v2").mkdir(parents=True, exist_ok=True)


def test_cli_pipeline_lsp_matrix_run_outputs_summary(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline lsp-matrix run은 실행 요약을 JSON으로 출력해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    class _FakePipelineLspMatrixService:
        """고정 매트릭스 결과를 반환하는 테스트 더블이다."""

        def run(
            self,
            repo_root: str,
            required_languages: tuple[str, ...] | None = None,
            fail_on_unavailable: bool = True,
            strict_all_languages: bool = True,
            strict_symbol_gate: bool = True,
        ) -> dict[str, object]:
            """테스트용 고정 응답을 반환한다."""
            _ = strict_symbol_gate
            return {
                "run_id": "run-1",
                "repo_root": repo_root,
                "summary": {"total_languages": 1, "available_languages": 1, "unavailable_languages": 0},
                "gate": {
                    "required_languages": list(required_languages or []),
                    "failed_required_languages": [],
                    "passed": True,
                    "fail_on_unavailable": fail_on_unavailable,
                    "strict_all_languages": strict_all_languages,
                },
                "languages": [
                    {
                        "language": "python",
                        "enabled": True,
                        "available": True,
                        "last_probe_at": "2026-02-17T00:00:00+00:00",
                        "last_error_code": None,
                        "last_error_message": None,
                    }
                ],
            }

    monkeypatch.setattr(
        "sari.cli.main._build_services",
        lambda: SimpleNamespace(pipeline_lsp_matrix_service=_FakePipelineLspMatrixService()),
    )

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir(parents=True, exist_ok=True)
    result = runner.invoke(cli, ["pipeline", "lsp-matrix", "run", "--repo", str(repo_dir.resolve())])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["lsp_matrix"]["summary"]["available_languages"] == 1
    assert payload["lsp_matrix"]["languages"][0]["language"] == "python"
