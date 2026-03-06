"""MCP 서버 프로토콜 동작을 검증한다."""

from pathlib import Path

from sari import __version__ as SARI_VERSION
from sari.core.exceptions import DaemonError, ErrorContext, ValidationError
from sari.core.models import ErrorResponseDTO, WorkspaceDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import DegradedMcpServer, McpServer


def test_mcp_initialize_and_tools_list(tmp_path: Path) -> None:
    """initialize와 tools/list 응답을 검증한다."""
    server = McpServer(db_path=tmp_path / "state.db")

    init_response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    init_payload = init_response.to_dict()
    assert "result" in init_payload
    assert init_payload["result"]["serverInfo"]["name"] == "sari-v2"
    assert init_payload["result"]["serverInfo"]["version"] == SARI_VERSION
    assert init_payload["result"]["schemaVersion"] == "2026-02-18.pack1.v2-line"
    assert init_payload["result"]["schema_version"] == "2026-02-18.pack1.v2-line"

    list_response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    list_payload = list_response.to_dict()
    assert list_payload["result"]["schemaVersion"] == "2026-02-18.pack1.v2-line"
    assert list_payload["result"]["schema_version"] == "2026-02-18.pack1.v2-line"
    tools = list_payload["result"]["tools"]
    tool_names = {tool["name"] for tool in tools}
    assert tool_names == {
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
    }
    tools_by_name = {tool["name"]: tool for tool in tools}
    search_props = tools_by_name["search"]["inputSchema"]["properties"]
    assert "repo" in search_props
    assert "x_examples" in tools_by_name["search"]
    server.close()


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
    text = payload["result"]["content"][0]["text"]
    assert "@ERR code=ERR_REPO_REQUIRED" in text


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
    text = payload["result"]["content"][0]["text"]
    assert "@ERR code=ERR_REPO_REQUIRED" in text


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
    text = payload["result"]["content"][0]["text"]
    assert "@ERR code=ERR_DB_MAPPING_INVALID" in text


def test_mcp_tool_call_returns_pack1_error_on_unexpected_exception(tmp_path: Path) -> None:
    """도구 내부 미처리 예외도 세션 종료 대신 pack1 오류로 반환되어야 한다."""

    class _BrokenTool:
        def call(self, arguments: dict[str, object]) -> dict[str, object]:
            _ = arguments
            raise RuntimeError("boom")

    server = McpServer(db_path=tmp_path / "state.db")
    server._doctor_tool = _BrokenTool()  # type: ignore[assignment]

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 51,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {"repo": "/repo"}},
        }
    )

    payload = response.to_dict()
    assert "error" not in payload
    assert payload["result"]["isError"] is True
    text = payload["result"]["content"][0]["text"]
    assert "@ERR code=ERR_MCP_TOOL_INTERNAL" in text
    assert "RuntimeError%3A%20boom" in text


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
    assert identify["version"] == SARI_VERSION
    assert identify["schemaVersion"] == "2026-02-18.pack1.v2-line"
    assert identify["schema_version"] == "2026-02-18.pack1.v2-line"
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
    server.close()


def test_degraded_tools_list_relaxes_repo_candidates_repo_requirement(tmp_path: Path) -> None:
    """degraded tools/list의 repo_candidates schema는 repo 없이도 호출 가능해야 한다."""
    server = DegradedMcpServer(
        db_path=tmp_path / "state.db",
        startup_error=ErrorResponseDTO(code="ERR_REPO_ID_INTEGRITY", message="broken repo_id"),
    )

    payload = server.handle_request({"jsonrpc": "2.0", "id": 23, "method": "tools/list"}).to_dict()

    tools = {str(tool["name"]): tool for tool in payload["result"]["tools"]}
    repo_candidates_schema = tools["repo_candidates"]["inputSchema"]
    assert "repo" not in repo_candidates_schema.get("required", [])


def test_degraded_tools_list_relaxes_status_and_doctor_repo_requirement(tmp_path: Path) -> None:
    """degraded tools/list의 status/doctor schema도 repo 없이 호출 가능해야 한다."""
    server = DegradedMcpServer(
        db_path=tmp_path / "state.db",
        startup_error=ErrorResponseDTO(code="ERR_REPO_ID_INTEGRITY", message="broken repo_id"),
    )

    payload = server.handle_request({"jsonrpc": "2.0", "id": 26, "method": "tools/list"}).to_dict()

    tools = {str(tool["name"]): tool for tool in payload["result"]["tools"]}
    for tool_name in ("status", "doctor"):
        input_schema = tools[tool_name]["inputSchema"]
        assert "repo" not in input_schema.get("required", [])


def test_degraded_status_tool_returns_structured_payload(tmp_path: Path) -> None:
    """degraded recovery 도구는 NameError 없이 정상 payload를 반환해야 한다."""
    server = DegradedMcpServer(
        db_path=tmp_path / "state.db",
        startup_error=ErrorResponseDTO(code="ERR_REPO_ID_INTEGRITY", message="broken repo_id"),
    )

    payload = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 24,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {"structured": 1}},
        }
    ).to_dict()

    assert payload["result"]["isError"] is False
    item = payload["result"]["structuredContent"]["items"][0]
    assert item["mcp_startup"]["state"] == "degraded"
    assert item["mcp_startup"]["reason_code"] == "ERR_REPO_ID_INTEGRITY"


def test_degraded_hidden_tool_call_remains_not_found(tmp_path: Path) -> None:
    """degraded tools/call에서도 hidden tool은 기존과 같이 not found로 숨겨야 한다."""
    server = DegradedMcpServer(
        db_path=tmp_path / "state.db",
        startup_error=ErrorResponseDTO(code="ERR_REPO_ID_INTEGRITY", message="broken repo_id"),
    )

    payload = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 25,
            "method": "tools/call",
            "params": {"name": "pipeline_auto_status", "arguments": {}},
        }
    ).to_dict()

    assert payload["error"]["code"] == -32601
    assert payload["error"]["message"] == "tool not found"


def test_mcp_status_includes_startup_state_when_healthy(tmp_path: Path) -> None:
    """status 응답은 MCP startup 상태를 additive 필드로 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = (tmp_path / "repo-a").resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=str(repo_root), name="repo-a", indexed_at=None, is_active=True))

    server = McpServer(db_path=db_path)
    payload = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 23,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {"repo": str(repo_root), "structured": 1}},
        }
    ).to_dict()

    assert payload["result"]["isError"] is False
    item = payload["result"]["structuredContent"]["items"][0]
    assert item["mcp_startup"] == {"state": "healthy", "reason_code": None, "reason_message": None}
    server.close()


def test_mcp_server_close_is_idempotent(tmp_path: Path) -> None:
    """MCP close는 중복 호출해도 실패하지 않아야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")
    server.close()
    server.close()


def test_mcp_server_close_raises_domain_error_when_hub_stop_fails(tmp_path: Path) -> None:
    """LSP hub 종료 실패는 ERR_MCP_CLOSE_FAILED로 감싸져야 한다."""

    class _BrokenHub:
        def stop_all(self) -> None:
            raise DaemonError(ErrorContext(code="ERR_DAEMON_UNAVAILABLE", message="hub stop failed"))

    server = McpServer(db_path=tmp_path / "state.db")
    server._managed_lsp_hubs = [_BrokenHub()]  # type: ignore[assignment]
    try:
        server.close()
    except DaemonError as exc:
        assert exc.context.code == "ERR_MCP_CLOSE_FAILED"
        assert "ERR_DAEMON_UNAVAILABLE" in exc.context.message
    else:
        raise AssertionError("DaemonError was not raised")
