"""HTTP LSP 매트릭스 엔드포인트를 검증한다."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from pathlib import Path

from sari.http.app import (
    HttpContext,
    pipeline_lsp_matrix_report_api_endpoint,
    pipeline_lsp_matrix_run_api_endpoint,
)


class _FakeLspMatrixService:
    """고정 run/report 응답을 반환하는 테스트 더블이다."""

    def __init__(self) -> None:
        """최신 결과를 저장할 내부 상태를 초기화한다."""
        self._latest: dict[str, object] | None = None

    def run(
        self,
        repo_root: str,
        required_languages: tuple[str, ...] | None = None,
        fail_on_unavailable: bool = True,
        strict_all_languages: bool = True,
        strict_symbol_gate: bool = True,
    ) -> dict[str, object]:
        """고정 매트릭스 실행 결과를 반환한다."""
        result = {
            "run_id": "run-1",
            "repo_root": repo_root,
            "summary": {
                "total_languages": 1,
                "available_languages": 1,
                "unavailable_languages": 0,
                "coverage_total_languages": 1,
                "coverage_checked_languages": 1,
                "readiness_percent": 100.0,
                "missing_server_languages": [],
            },
            "gate": {
                "required_languages": list(required_languages or []),
                "failed_required_languages": [],
                "passed": True,
                "fail_on_unavailable": fail_on_unavailable,
                "strict_all_languages": strict_all_languages,
                "strict_symbol_gate": strict_symbol_gate,
                "pass_threshold_percent": 98.0,
                "critical_passed": True,
                "gate_decision": "PASS",
            },
            "languages": [],
        }
        self._latest = result
        return result

    def get_latest_report(self, repo_root: str) -> dict[str, object]:
        """최신 리포트를 반환한다."""
        _ = repo_root
        assert self._latest is not None
        return self._latest


def test_http_pipeline_lsp_matrix_run_and_report(tmp_path: Path) -> None:
    """run/report API가 정상 응답을 반환해야 한다."""
    service = _FakeLspMatrixService()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    workspace_repo = SimpleNamespace(list_all=lambda: [SimpleNamespace(path=str(repo_dir.resolve()), is_active=True)])
    context = HttpContext(
        runtime_repo=SimpleNamespace(),
        workspace_repo=workspace_repo,
        search_orchestrator=SimpleNamespace(),
        admin_service=SimpleNamespace(),
        pipeline_lsp_matrix_service=service,
    )
    run_request = SimpleNamespace(
        query_params={
            "repo": str(repo_dir.resolve()),
            "required_languages": "python,typescript",
            "fail_on_unavailable": "false",
            "strict_all_languages": "false",
            "strict_symbol_gate": "false",
        },
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    run_response = asyncio.run(pipeline_lsp_matrix_run_api_endpoint(run_request))
    assert run_response.status_code == 200
    run_payload = json.loads(run_response.body.decode("utf-8"))
    assert run_payload["lsp_matrix"]["gate"]["required_languages"] == ["python", "typescript"]
    assert run_payload["lsp_matrix"]["gate"]["fail_on_unavailable"] is False
    assert run_payload["lsp_matrix"]["gate"]["strict_all_languages"] is False
    assert run_payload["lsp_matrix"]["gate"]["strict_symbol_gate"] is False
    assert run_payload["lsp_matrix"]["summary"]["missing_server_languages"] == []

    report_request = SimpleNamespace(
        query_params={"repo": str(repo_dir.resolve())},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    report_response = asyncio.run(pipeline_lsp_matrix_report_api_endpoint(report_request))
    assert report_response.status_code == 200
    report_payload = json.loads(report_response.body.decode("utf-8"))
    assert report_payload["lsp_matrix"]["run_id"] == "run-1"
