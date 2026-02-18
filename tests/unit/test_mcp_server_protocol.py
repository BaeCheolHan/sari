"""MCP 서버 프로토콜 동작을 검증한다."""

from pathlib import Path

from pytest import MonkeyPatch
from sari.core.exceptions import ErrorContext, ValidationError
from sari.core.models import WorkspaceDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer


def test_mcp_initialize_and_tools_list(tmp_path: Path) -> None:
    """initialize와 tools/list 응답을 검증한다."""
    server = McpServer(db_path=tmp_path / "state.db")

    init_response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    init_payload = init_response.to_dict()
    assert "result" in init_payload
    assert init_payload["result"]["serverInfo"]["name"] == "sari-v2"

    list_response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    list_payload = list_response.to_dict()
    tools = list_payload["result"]["tools"]
    tool_names = {tool["name"] for tool in tools}
    assert tool_names == {
        "archive_context",
        "call_graph",
        "call_graph_health",
        "search",
        "doctor",
        "status",
        "sari_guide",
        "rescan",
        "repo_candidates",
        "read",
        "dry_run_diff",
        "scan_once",
        "list_files",
        "read_file",
        "index_file",
        "list_symbols",
        "read_symbol",
        "search_symbol",
        "get_callers",
        "get_implementations",
        "knowledge",
        "save_snippet",
        "get_snippet",
        "get_context",
        "pipeline_policy_get",
        "pipeline_policy_set",
        "pipeline_alert_status",
        "pipeline_dead_list",
        "pipeline_dead_requeue",
        "pipeline_dead_purge",
        "pipeline_auto_status",
        "pipeline_auto_set",
        "pipeline_auto_tick",
        "pipeline_benchmark_run",
        "pipeline_benchmark_report",
        "pipeline_quality_run",
        "pipeline_quality_report",
        "pipeline_lsp_matrix_run",
        "pipeline_lsp_matrix_report",
    }
    tools_by_name = {tool["name"]: tool for tool in tools}
    benchmark_props = tools_by_name["pipeline_benchmark_run"]["inputSchema"]["properties"]
    assert "language_filter" in benchmark_props
    assert "per_language_report" in benchmark_props
    quality_props = tools_by_name["pipeline_quality_run"]["inputSchema"]["properties"]
    assert "language_filter" in quality_props
    lsp_matrix_props = tools_by_name["pipeline_lsp_matrix_run"]["inputSchema"]["properties"]
    assert "required_languages" in lsp_matrix_props
    assert "fail_on_unavailable" in lsp_matrix_props
    assert "strict_all_languages" in lsp_matrix_props
    assert "strict_symbol_gate" in lsp_matrix_props


def test_mcp_tool_call_requires_repo_and_query(tmp_path: Path) -> None:
    """search 도구 입력 검증 오류를 확인한다."""
    server = McpServer(db_path=tmp_path / "state.db")

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "abc", "limit": 5}},
        }
    )

    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    assert payload["result"]["structuredContent"]["meta"]["errors"][0]["code"] == "ERR_REPO_REQUIRED"
    assert payload["result"]["structuredContent"]["meta"]["errors"][0]["message"] == "repo is required"


def test_mcp_doctor_requires_repo(tmp_path: Path) -> None:
    """doctor 도구도 repo 파라미터를 필수로 요구해야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {}},
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    assert payload["result"]["structuredContent"]["meta"]["errors"][0]["code"] == "ERR_REPO_REQUIRED"
    assert payload["result"]["structuredContent"]["meta"]["errors"][0]["message"] == "repo is required"


def test_mcp_tool_call_returns_pack1_error_on_validation_exception(tmp_path: Path) -> None:
    """도구 내부 ValidationError는 JSON-RPC 성공 + pack1 오류로 변환되어야 한다."""

    class _BrokenTool:
        """호출 즉시 ValidationError를 발생시키는 테스트 더블이다."""

        def call(self, arguments: dict[str, object]) -> dict[str, object]:
            """ValidationError를 발생시킨다."""
            _ = arguments
            raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message="invalid row"))

    server = McpServer(db_path=tmp_path / "state.db")
    server._doctor_tool = _BrokenTool()  # type: ignore[assignment]

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {"repo": "/repo"}},
        }
    )

    payload = response.to_dict()
    assert "error" not in payload
    assert payload["result"]["isError"] is True
    err = payload["result"]["structuredContent"]["meta"]["errors"][0]
    assert err["code"] == "ERR_DB_MAPPING_INVALID"
    assert err["message"] == "invalid row"


def test_mcp_supports_method_surface_parity(tmp_path: Path) -> None:
    """표준 MCP 보조 메서드와 확장 메서드를 지원해야 한다."""
    db_path = tmp_path / "state.db"
    server = McpServer(db_path=db_path)

    prompts_payload = server.handle_request({"jsonrpc": "2.0", "id": 11, "method": "prompts/list"}).to_dict()
    assert prompts_payload["result"]["prompts"] == []

    resources_payload = server.handle_request({"jsonrpc": "2.0", "id": 12, "method": "resources/list"}).to_dict()
    assert resources_payload["result"]["resources"] == []

    templates_payload = server.handle_request(
        {"jsonrpc": "2.0", "id": 13, "method": "resources/templates/list"}
    ).to_dict()
    assert templates_payload["result"]["resourceTemplates"] == []

    ping_payload = server.handle_request({"jsonrpc": "2.0", "id": 14, "method": "ping"}).to_dict()
    assert ping_payload["result"] == {}

    initialized_payload = server.handle_request({"jsonrpc": "2.0", "id": 15, "method": "initialized"}).to_dict()
    assert initialized_payload["result"] == {}

    notify_initialized_payload = server.handle_request(
        {"jsonrpc": "2.0", "id": 16, "method": "notifications/initialized"}
    ).to_dict()
    assert notify_initialized_payload["result"] == {}

    identify_payload = server.handle_request({"jsonrpc": "2.0", "id": 17, "method": "sari/identify"}).to_dict()
    identify = identify_payload["result"]
    assert identify["name"] == "sari-v2"
    assert identify["version"] == "0.1.0"
    assert isinstance(identify["pid"], int)
    assert "workspaceRoot" in identify


def test_mcp_roots_list_uses_registered_workspaces(tmp_path: Path) -> None:
    """roots/list는 등록된 워크스페이스를 file URI로 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = WorkspaceRepository(db_path)
    path_a = (tmp_path / "repo-a").resolve()
    path_b = (tmp_path / "repo-b").resolve()
    path_a.mkdir(parents=True, exist_ok=True)
    path_b.mkdir(parents=True, exist_ok=True)
    repo.add(WorkspaceDTO(path=str(path_a), name="repo-a", indexed_at=None, is_active=True))
    repo.add(WorkspaceDTO(path=str(path_b), name="repo-b", indexed_at=None, is_active=True))

    server = McpServer(db_path=db_path)
    payload = server.handle_request({"jsonrpc": "2.0", "id": 18, "method": "roots/list"}).to_dict()

    roots = payload["result"]["roots"]
    assert len(roots) == 2
    uris = {item["uri"] for item in roots}
    assert f"file://{path_a}" in uris
    assert f"file://{path_b}" in uris


def test_mcp_initialize_negotiates_supported_protocol_version(tmp_path: Path) -> None:
    """initialize는 클라이언트 제안 버전과 협상해야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")
    payload = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 19,
            "method": "initialize",
            "params": {"supportedProtocolVersions": ["2025-03-26", "2024-11-05"]},
        }
    ).to_dict()
    assert payload["result"]["protocolVersion"] == "2025-03-26"


def test_mcp_initialize_strict_protocol_rejects_unknown_versions(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """strict protocol 모드에서 미지원 버전은 명시적으로 실패해야 한다."""
    monkeypatch.setenv("SARI_STRICT_PROTOCOL", "1")
    server = McpServer(db_path=tmp_path / "state.db")
    payload = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "initialize",
            "params": {"supportedProtocolVersions": ["2099-01-01"]},
        }
    ).to_dict()
    assert payload["error"]["code"] == -32602
    assert payload["error"]["message"] == "Unsupported protocol version"


def test_mcp_initialize_and_tools_list_do_not_touch_tantivy_writer(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """initialize/tools/list는 Tantivy writer 잠금과 무관하게 동작해야 한다."""

    def _raise_if_writer_called(self: object) -> object:
        del self
        raise AssertionError("tantivy writer must not be touched in initialize/tools-list path")

    monkeypatch.setattr("sari.search.candidate_search.TantivyCandidateBackend._get_writer", _raise_if_writer_called)
    server = McpServer(db_path=tmp_path / "state.db")

    init_payload = server.handle_request({"jsonrpc": "2.0", "id": 21, "method": "initialize"}).to_dict()
    list_payload = server.handle_request({"jsonrpc": "2.0", "id": 22, "method": "tools/list"}).to_dict()

    assert "error" not in init_payload
    assert "error" not in list_payload
    assert init_payload["result"]["serverInfo"]["name"] == "sari-v2"
    assert isinstance(list_payload["result"]["tools"], list)
