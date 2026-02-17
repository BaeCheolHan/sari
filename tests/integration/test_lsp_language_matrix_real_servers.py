"""실서버 LSP 매트릭스 E2E를 검증한다."""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner
import pytest

from sari.cli.main import cli


def test_real_lsp_matrix_reports_missing_server_consistently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """실서버 실행에서 ERR_LSP_SERVER_MISSING 집계와 summary가 일치해야 한다."""
    if os.getenv("SARI_E2E_REAL_LSP", "").strip() != "1":
        pytest.skip("real lsp e2e is disabled")

    repo_root = os.getenv("SARI_E2E_REAL_LSP_REPO", "").strip()
    if repo_root == "":
        pytest.skip("SARI_E2E_REAL_LSP_REPO is required for real e2e")

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".local" / "share" / "sari-v2").mkdir(parents=True, exist_ok=True)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "pipeline",
            "lsp-matrix",
            "run",
            "--repo",
            str(Path(repo_root).expanduser().resolve()),
            "--fail-on-unavailable",
            "false",
            "--strict-all-languages",
            "true",
            "--strict-symbol-gate",
            "true",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    matrix = payload["lsp_matrix"]
    summary = matrix["summary"]
    languages = matrix["languages"]

    missing_from_languages = sorted(
        str(item["language"]).strip().lower()
        for item in languages
        if isinstance(item, dict) and str(item.get("last_error_code", "")).strip() == "ERR_LSP_SERVER_MISSING"
    )
    assert summary["missing_server_languages"] == missing_from_languages
