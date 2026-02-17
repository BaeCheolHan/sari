"""CLI LSP 매트릭스 진단 명령을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from sari.cli.main import cli
from sari.services.lsp_matrix_diagnose_service import LspMatrixDiagnoseService


class _FakeLspMatrixService:
    """고정 매트릭스 리포트를 반환하는 테스트 더블이다."""

    def get_latest_report(self, repo_root: str) -> dict[str, object]:
        """최신 리포트를 반환한다."""
        return {
            "run_id": "run-1",
            "repo_root": repo_root,
            "summary": {"readiness_percent": 95.0, "symbol_extract_success_rate": 92.0},
            "gate": {"gate_decision": "FAIL", "failed_symbol_languages": ["python"]},
            "languages": [
                {"language": "python", "last_error_code": "ERR_LSP_SERVER_MISSING", "symbol_extract_success": False},
                {"language": "go", "last_error_code": None, "symbol_extract_success": True},
            ],
        }


def test_cli_pipeline_lsp_matrix_diagnose_writes_artifacts(monkeypatch, tmp_path: Path) -> None:
    """diagnose 명령은 진단 JSON과 Markdown 아티팩트를 저장해야 한다."""
    runner = CliRunner()
    monkeypatch.setattr(
        "sari.cli.main._build_services",
        lambda: SimpleNamespace(
            pipeline_lsp_matrix_service=_FakeLspMatrixService(),
            lsp_matrix_diagnose_service=LspMatrixDiagnoseService(),
        ),
    )
    output_dir = tmp_path / "artifacts"
    result = runner.invoke(
        cli,
        [
            "pipeline",
            "lsp-matrix",
            "diagnose",
            "--repo",
            "/repo",
            "--mode",
            "latest",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["diagnose"]["missing_server_languages"] == ["python"]
    json_path = Path(payload["artifacts"]["json"])
    md_path = Path(payload["artifacts"]["markdown"])
    assert json_path.exists()
    assert md_path.exists()
