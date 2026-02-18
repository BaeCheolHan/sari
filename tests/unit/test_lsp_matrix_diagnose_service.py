"""LSP 매트릭스 진단 서비스를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.services.lsp_matrix_diagnose_service import LspMatrixDiagnoseService


def test_lsp_matrix_diagnose_service_builds_expected_groups(tmp_path: Path) -> None:
    """오류코드/심볼실패를 그룹화해 진단 요약을 생성해야 한다."""
    service = LspMatrixDiagnoseService()
    matrix_report = {
        "run_id": "run-1",
        "repo_root": "/repo",
        "summary": {
            "readiness_percent": 91.0,
            "symbol_extract_success_rate": 84.5,
        },
        "gate": {
            "gate_decision": "FAIL",
            "failed_symbol_languages": ["typescript"],
        },
        "languages": [
            {"language": "python", "last_error_code": "ERR_LSP_SERVER_MISSING", "symbol_extract_success": False},
            {"language": "typescript", "last_error_code": "ERR_LSP_TIMEOUT", "symbol_extract_success": False, "timeout_occurred": True},
            {"language": "java", "last_error_code": "ERR_LSP_DOCUMENT_SYMBOL_FAILED", "symbol_extract_success": False},
            {"language": "go", "last_error_code": None, "symbol_extract_success": True},
        ],
    }

    diagnosis = service.diagnose(matrix_report=matrix_report)
    assert diagnosis["missing_server_languages"] == ["python"]
    assert diagnosis["timeout_languages"] == ["typescript"]
    assert diagnosis["symbol_failed_languages"] == ["java", "python", "typescript"]
    assert diagnosis["error_code_counts"]["ERR_LSP_SERVER_MISSING"] == 1
    assert diagnosis["error_code_counts"]["ERR_LSP_TIMEOUT"] == 1
    policies = diagnosis["language_policies"]
    assert isinstance(policies, list)
    python_policy = [item for item in policies if item["language"] == "python"][0]
    assert python_policy["provisioning_mode"] == "hybrid"
    high_actions = [item for item in diagnosis["recommended_actions"] if item.get("severity") == "HIGH"]
    assert len(high_actions) == 1
    assert "recovery_hint" in high_actions[0]

    json_path, md_path = service.write_outputs(diagnosis=diagnosis, output_dir=tmp_path / "out")
    assert json_path.exists()
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "Missing Servers" in content
    assert "Provisioning Policies" in content
    assert "python" in content


def test_lsp_matrix_diagnose_service_includes_gate_run_metadata() -> None:
    """게이트 실행 메타(run_id/repair/rerun/final_decision)를 진단 결과에 포함해야 한다."""
    service = LspMatrixDiagnoseService()
    matrix_report = {
        "run_id": "run-2",
        "repo_root": "/repo",
        "gate": {"gate_decision": "FAIL"},
        "languages": [],
        "gate_run": {
            "repair_applied": True,
            "rerun_count": 1,
            "gate_mode": "report-only",
            "final_gate_decision": "FAIL",
        },
    }

    diagnosis = service.diagnose(matrix_report=matrix_report)
    assert diagnosis["gate_mode"] == "report-only"
    assert diagnosis["repair_applied"] is True
    assert diagnosis["rerun_count"] == 1
    assert diagnosis["final_gate_decision"] == "FAIL"
