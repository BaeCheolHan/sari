"""HTTP 라우트 목록을 구성한다."""

from __future__ import annotations

from starlette.routing import Route

from sari.http.admin_endpoints import (
    daemon_list_endpoint,
    daemon_reconcile_endpoint,
    doctor_endpoint,
    errors_endpoint,
    repo_candidates_endpoint,
    rescan_endpoint,
)
from sari.http.meta_endpoints import health_endpoint, mcp_jsonrpc_endpoint, status_endpoint, workspaces_endpoint
from sari.http.pipeline_endpoints import (
    pipeline_alert_endpoint,
    pipeline_auto_set_endpoint,
    pipeline_auto_status_endpoint,
    pipeline_auto_tick_endpoint,
    pipeline_dead_list_endpoint,
    pipeline_dead_purge_endpoint,
    pipeline_dead_requeue_endpoint,
    pipeline_lsp_matrix_report_api_endpoint,
    pipeline_lsp_matrix_run_api_endpoint,
    pipeline_perf_report_api_endpoint,
    pipeline_perf_run_api_endpoint,
    pipeline_policy_get_endpoint,
    pipeline_policy_set_endpoint,
    pipeline_quality_report_api_endpoint,
    pipeline_quality_run_api_endpoint,
)
from sari.http.pipeline_error_endpoints import (
    pipeline_error_detail_api_endpoint,
    pipeline_error_detail_html_endpoint,
    pipeline_errors_api_endpoint,
    pipeline_errors_html_endpoint,
)
from sari.http.read_endpoints import read_diff_preview_endpoint, read_endpoint, read_file_endpoint, read_snippet_endpoint, read_symbol_endpoint
from sari.http.search_endpoints import search_endpoint


def build_http_routes() -> list[Route]:
    return [
        Route("/health", health_endpoint),
        Route("/status", status_endpoint),
        Route("/workspaces", workspaces_endpoint),
        Route("/mcp", mcp_jsonrpc_endpoint, methods=["POST"]),
        Route("/search", search_endpoint),
        Route("/read", read_endpoint, methods=["GET"]),
        Route("/read_file", read_file_endpoint, methods=["GET"]),
        Route("/read_symbol", read_symbol_endpoint, methods=["GET"]),
        Route("/read_snippet", read_snippet_endpoint, methods=["GET"]),
        Route("/read_diff_preview", read_diff_preview_endpoint, methods=["POST"]),
        Route("/errors", errors_endpoint),
        Route("/rescan", rescan_endpoint),
        Route("/repo-candidates", repo_candidates_endpoint),
        Route("/doctor", doctor_endpoint),
        Route("/daemon/list", daemon_list_endpoint),
        Route("/daemon/reconcile", daemon_reconcile_endpoint, methods=["POST"]),
        Route("/pipeline/policy", pipeline_policy_get_endpoint, methods=["GET"]),
        Route("/pipeline/policy", pipeline_policy_set_endpoint, methods=["POST"]),
        Route("/pipeline/alert", pipeline_alert_endpoint, methods=["GET"]),
        Route("/pipeline/dead", pipeline_dead_list_endpoint, methods=["GET"]),
        Route("/pipeline/dead/requeue", pipeline_dead_requeue_endpoint, methods=["POST"]),
        Route("/pipeline/dead/purge", pipeline_dead_purge_endpoint, methods=["POST"]),
        Route("/pipeline/auto/status", pipeline_auto_status_endpoint, methods=["GET"]),
        Route("/pipeline/auto/set", pipeline_auto_set_endpoint, methods=["POST"]),
        Route("/pipeline/auto/tick", pipeline_auto_tick_endpoint, methods=["POST"]),
        Route("/api/pipeline/errors", pipeline_errors_api_endpoint, methods=["GET"]),
        Route("/api/pipeline/errors/{event_id:str}", pipeline_error_detail_api_endpoint, methods=["GET"]),
        Route("/api/pipeline/perf/run", pipeline_perf_run_api_endpoint, methods=["POST"]),
        Route("/api/pipeline/perf", pipeline_perf_report_api_endpoint, methods=["GET"]),
        Route("/api/pipeline/quality/run", pipeline_quality_run_api_endpoint, methods=["POST"]),
        Route("/api/pipeline/quality", pipeline_quality_report_api_endpoint, methods=["GET"]),
        Route("/api/pipeline/lsp-matrix/run", pipeline_lsp_matrix_run_api_endpoint, methods=["POST"]),
        Route("/api/pipeline/lsp-matrix", pipeline_lsp_matrix_report_api_endpoint, methods=["GET"]),
        Route("/pipeline/errors", pipeline_errors_html_endpoint, methods=["GET"]),
        Route("/pipeline/errors/{event_id:str}", pipeline_error_detail_html_endpoint, methods=["GET"]),
    ]
