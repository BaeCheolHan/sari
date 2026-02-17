"""CLI LSP 매트릭스 명령을 검증한다."""

from __future__ import annotations

import json
from types import SimpleNamespace

from click.testing import CliRunner

from sari.cli.main import cli


class _FakeLspMatrixService:
    """고정 매트릭스 응답을 반환하는 테스트 더블이다."""

    def run(
        self,
        repo_root: str,
        required_languages: tuple[str, ...] | None = None,
        fail_on_unavailable: bool = True,
        strict_all_languages: bool = True,
        strict_symbol_gate: bool = True,
    ) -> dict[str, object]:
        """실행 결과를 반환한다."""
        return {
            "run_id": "run-1",
            "repo_root": repo_root,
            "summary": {
                "total_languages": 1,
                "available_languages": 0,
                "unavailable_languages": 1,
                "coverage_total_languages": 1,
                "coverage_checked_languages": 1,
                "readiness_percent": 0.0,
                "missing_server_languages": [],
            },
            "gate": {
                "required_languages": list(required_languages or []),
                "failed_required_languages": ["python"] if fail_on_unavailable else [],
                "passed": not fail_on_unavailable,
                "fail_on_unavailable": fail_on_unavailable,
                "strict_all_languages": strict_all_languages,
                "strict_symbol_gate": strict_symbol_gate,
                "pass_threshold_percent": 98.0,
                "critical_passed": not fail_on_unavailable,
                "gate_decision": ("PASS" if not fail_on_unavailable else "FAIL"),
            },
            "languages": [],
        }


def test_cli_pipeline_lsp_matrix_run_parses_options(monkeypatch) -> None:
    """required_languages/fail_on_unavailable 옵션이 서비스로 전달되어야 한다."""
    runner = CliRunner()
    monkeypatch.setattr(
        "sari.cli.main._build_services",
        lambda: SimpleNamespace(pipeline_lsp_matrix_service=_FakeLspMatrixService()),
    )
    result = runner.invoke(
        cli,
        [
            "pipeline",
            "lsp-matrix",
            "run",
            "--repo",
            "/repo",
            "--required-language",
            "python",
            "--required-language",
            "typescript",
            "--fail-on-unavailable",
            "false",
            "--strict-all-languages",
            "false",
            "--strict-symbol-gate",
            "false",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["lsp_matrix"]["gate"]["required_languages"] == ["python", "typescript"]
    assert payload["lsp_matrix"]["gate"]["fail_on_unavailable"] is False
    assert payload["lsp_matrix"]["gate"]["strict_all_languages"] is False
    assert payload["lsp_matrix"]["gate"]["strict_symbol_gate"] is False
    assert payload["lsp_matrix"]["summary"]["missing_server_languages"] == []
