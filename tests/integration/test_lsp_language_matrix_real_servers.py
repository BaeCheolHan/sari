"""실서버 LSP 매트릭스 E2E를 검증한다."""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner
import pytest

from sari.cli.main import cli


def _parse_json_from_mixed_output(raw_output: str) -> dict[str, object]:
    """로그가 섞인 stdout에서 마지막 JSON 객체를 추출한다."""
    stripped = raw_output.strip()
    if stripped == "":
        raise AssertionError("empty CLI output")
    for line in reversed(stripped.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AssertionError(f"JSON payload not found in output head={stripped[:200]!r}")


def test_real_lsp_matrix_reports_missing_server_consistently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """실서버 실행에서 ERR_LSP_SERVER_MISSING 집계와 summary가 일치해야 한다."""
    repo_root = os.getenv("SARI_E2E_REAL_LSP_REPO", "").strip()
    if repo_root == "":
        pytest.skip("SARI_E2E_REAL_LSP_REPO is required for real e2e")
    resolved_repo_root = str(Path(repo_root).expanduser().resolve())

    db_path = tmp_path / "state.db"
    monkeypatch.setenv("SARI_DB_PATH", str(db_path))

    runner = CliRunner()
    add_result = runner.invoke(
        cli,
        [
            "roots",
            "add",
            resolved_repo_root,
        ],
    )
    assert add_result.exit_code == 0, add_result.output

    result = runner.invoke(
        cli,
        [
            "pipeline",
            "lsp-matrix",
            "run",
            "--repo",
            resolved_repo_root,
            "--fail-on-unavailable",
            "false",
            "--strict-all-languages",
            "false",
            "--strict-symbol-gate",
            "false",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _parse_json_from_mixed_output(result.output)
    matrix = payload["lsp_matrix"]
    summary = matrix["summary"]
    languages = matrix["languages"]

    missing_from_languages = sorted(
        str(item["language"]).strip().lower()
        for item in languages
        if isinstance(item, dict) and str(item.get("last_error_code", "")).strip() == "ERR_LSP_SERVER_MISSING"
    )
    assert summary["missing_server_languages"] == missing_from_languages
