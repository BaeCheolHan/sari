
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import sari.mcp.tools.search as search_tool
import sari.mcp.tools.status as status_tool
import sari.mcp.tools.repo_candidates as repo_candidates_tool
import sari.mcp.tools.list_files as list_files_tool
import sari.mcp.tools.list_symbols as list_symbols_tool
import sari.mcp.tools.read_file as read_file_tool
import sari.mcp.tools.grep_and_read as grep_and_read_tool
import sari.mcp.tools.search_symbols as search_symbols_tool
import sari.mcp.tools.read_symbol as read_symbol_tool
import sari.mcp.tools.doctor as doctor_tool
import sari.mcp.tools.search_api_endpoints as search_api_endpoints_tool
import sari.mcp.tools.index_file as index_file_tool
import sari.mcp.tools.rescan as rescan_tool
import sari.mcp.tools.scan_once as scan_once_tool
import sari.mcp.tools.get_callers as get_callers_tool
import sari.mcp.tools.get_implementations as get_implementations_tool
import sari.mcp.tools.guide as guide_tool
import sari.mcp.tools.call_graph as call_graph_tool
import sari.mcp.tools.call_graph_health as call_graph_health_tool
import sari.mcp.tools.save_snippet as save_snippet_tool
import sari.mcp.tools.get_snippet as get_snippet_tool
import sari.mcp.tools.archive_context as archive_context_tool
import sari.mcp.tools.get_context as get_context_tool
import sari.mcp.tools.dry_run_diff as dry_run_diff_tool


@dataclass
class Tool:
    """
    MCP 도구 정의 데이터 클래스.
    이름, 설명, 입력 스키마 및 실행 핸들러 정보를 포함합니다.
    """
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[["ToolContext", Dict[str, Any]], Dict[str, Any]]
    hidden: bool = False


@dataclass
class ToolContext:
    """
    도구 실행 시 제공되는 컨텍스트 정보.
    DB 접근 주체, 인덱서, 설정 및 환경 정보를 캡슐화합니다.
    """
    db: Any
    engine: Any
    indexer: Any
    roots: List[str]
    cfg: Any
    logger: Any
    workspace_root: str
    server_version: str
    policy_engine: Optional[Any] = None  # 정책 추적용 추가 필드


class ToolRegistry:
    """
    Sari MCP 도구들을 등록하고 관리하며 실행을 위임하는 레지스트리 클래스입니다.
    """
    
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """도구를 레지스트리에 등록합니다."""
        self._tools[tool.name] = tool

    def list_tools_raw(self) -> List[Tool]:
        """등록된 원본 도구 객체 목록을 반환합니다."""
        return list(self._tools.values())

    def list_tools(self) -> List[Dict[str, Any]]:
        """
        MCP 프로토콜 규격에 맞게 도구 정의(JSON Schema 포함) 목록을 반환합니다.
        `SARI_EXPOSE_INTERNAL_TOOLS` 설정에 따라 숨겨진 도구 노출 여부를 결정합니다.
        """
        expose_internal = os.environ.get("SARI_EXPOSE_INTERNAL_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in self._tools.values()
            if (not t.hidden) or expose_internal
        ]

    def execute(self, name: str, ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        요청된 도구를 찾아 실행하고 결과를 반환합니다.
        정책 엔진(Policy Engine)이 활성화된 경우 실행 이력을 마킹합니다.
        """
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
            
        result = tool.handler(ctx, args)

        def _is_error_response(res: Dict[str, Any]) -> bool:
            if bool(res.get("isError")):
                return True
            try:
                content = res.get("content") or []
                if not content:
                    return False
                text = str((content[0] or {}).get("text") or "")
                if not text:
                    return False
                first_line = text.splitlines()[0]
                if first_line.startswith("PACK1 ") and " ok=false" in first_line:
                    return True
            except Exception:
                return False
            return False

        # 실행 정책 훅: 검색 액션 자동 마킹
        if ctx.policy_engine and not _is_error_response(result):
            if name in {"search", "search_symbols", "grep_and_read"}:
                ctx.policy_engine.mark_action(name)
                
        return result


def build_default_registry() -> ToolRegistry:
    """기본 도구 레지스트리를 생성하고 모든 도구 그룹을 등록하여 반환합니다."""
    reg = ToolRegistry()
    _register_core_tools(reg)      # 핵심 도구
    _register_search_tools(reg)    # 검색 도구
    _register_file_tools(reg)      # 파일 조작 도구
    _register_symbol_tools(reg)    # 심볼 분석 도구
    _register_knowledge_tools(reg) # 지식 저장 도구
    return reg


def _register_core_tools(reg: ToolRegistry):
    """핵심 기능 관련 도구 등록 (가이드, 상태, 진단 등)"""
    reg.register(Tool(
        name="sari_guide",
        description="Usage guide. Call this if unsure; it enforces search-first workflow.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda ctx, args: guide_tool.execute_sari_guide(args),
    ))

    reg.register(Tool(
        name="status",
        description="Get indexer status. Use details=true for per-repo stats.",
        input_schema={"type": "object", "properties": {"details": {"type": "boolean", "default": False}}},
        handler=lambda ctx, args: status_tool.execute_status(args, ctx.indexer, ctx.db, ctx.cfg, ctx.workspace_root, ctx.server_version, ctx.logger),
    ))

    reg.register(Tool(
        name="rescan",
        description="(Internal) Trigger an async rescan of the workspace index.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda ctx, args: rescan_tool.execute_rescan(args, ctx.indexer),
        hidden=True,
    ))

    reg.register(Tool(
        name="scan_once",
        description="(Internal) Run a synchronous scan once (blocking).",
        input_schema={"type": "object", "properties": {}},
        handler=lambda ctx, args: scan_once_tool.execute_scan_once(args, ctx.indexer, ctx.logger),
        hidden=True,
    ))

    reg.register(Tool(
        name="doctor",
        description="Run health checks and return structured diagnostics.",
        input_schema={
            "type": "object",
            "properties": {
                "include_network": {"type": "boolean", "default": True},
                "include_port": {"type": "boolean", "default": True},
                "include_db": {"type": "boolean", "default": True},
                "include_disk": {"type": "boolean", "default": True},
                "include_daemon": {"type": "boolean", "default": True},
                "include_venv": {"type": "boolean", "default": True},
                "include_marker": {"type": "boolean", "default": False},
                "port": {"type": "integer", "default": 47800},
                "min_disk_gb": {"type": "number", "default": 1.0},
            },
        },
        handler=lambda ctx, args: doctor_tool.execute_doctor(args),
    ))


def _register_search_tools(reg: ToolRegistry):
    """검색 및 탐색 관련 도구 등록"""
    reg.register(Tool(
        name="search",
        description="SEARCH FIRST. MANDATORY before reading files. Use to locate relevant paths/symbols. Prevents token waste by narrowing scope.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (keywords, function names, regex)"},
                "repo": {"type": "string", "description": "Limit search to specific repository"},
                "limit": {"type": "integer", "description": "Maximum results (default: 10, max: 50)", "default": 10},
                "offset": {"type": "integer", "description": "Pagination offset (default: 0)", "default": 0},
                "file_types": {"type": "array", "items": {"type": "string"}, "description": "Filter by file extensions"},
                "path_pattern": {"type": "string", "description": "Glob pattern for path matching"},
                "exclude_patterns": {"type": "array", "items": {"type": "string"}, "description": "Patterns to exclude"},
                "recency_boost": {"type": "boolean", "description": "Boost recently modified files", "default": False},
                "use_regex": {"type": "boolean", "description": "Treat query as regex pattern", "default": False},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive search", "default": False},
                "context_lines": {"type": "integer", "description": "Number of context lines in snippet", "default": 5},
                "total_mode": {"type": "string", "enum": ["exact", "approx"], "description": "Total count mode"},
                "root_ids": {"type": "array", "items": {"type": "string"}, "description": "Limit search to specific root_ids"},
                "scope": {"type": "string", "description": "Alias for 'repo'"},
                "type": {"type": "string", "enum": ["docs", "code"], "description": "Filter by type: 'docs' or 'code'"},
            },
            "required": ["query"],
        },
        handler=lambda ctx, args: search_tool.execute_search(args, ctx.db, ctx.logger, ctx.roots, engine=ctx.engine, indexer=ctx.indexer),
    ))

    reg.register(Tool(
        name="grep_and_read",
        description="Composite tool: Search and immediately read top snippets. BEST for quick investigation without reading full files.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "repo": {"type": "string", "description": "Limit search to specific repository"},
                "limit": {"type": "integer", "description": "Maximum search results (default: 8)", "default": 8},
                "read_limit": {"type": "integer", "description": "Files to read from top results (default: 3)", "default": 3},
                "file_types": {"type": "array", "items": {"type": "string"}, "description": "Filter by file extensions"},
                "path_pattern": {"type": "string", "description": "Glob pattern for path matching"},
                "exclude_patterns": {"type": "array", "items": {"type": "string"}, "description": "Patterns to exclude"},
                "recency_boost": {"type": "boolean", "description": "Boost recently modified files", "default": False},
                "use_regex": {"type": "boolean", "description": "Treat query as regex pattern", "default": False},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive search", "default": False},
                "context_lines": {"type": "integer", "description": "Number of context lines in snippet", "default": 5},
                "total_mode": {"type": "string", "enum": ["exact", "approx"], "description": "Total count mode"},
                "root_ids": {"type": "array", "items": {"type": "string"}, "description": "Limit search to specific root_ids"},
                "scope": {"type": "string", "description": "Alias for 'repo'"},
            },
            "required": ["query"],
        },
        handler=lambda ctx, args: grep_and_read_tool.execute_grep_and_read(args, ctx.db, ctx.roots),
    ))
    
    reg.register(Tool(
        name="repo_candidates",
        description="Suggest top repos for a query. Use before search if repo is unknown.",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 3}}, "required": ["query"]},
        handler=lambda ctx, args: repo_candidates_tool.execute_repo_candidates(args, ctx.db, ctx.logger, ctx.roots),
    ))

    reg.register(Tool(
        name="search_api_endpoints",
        description="Search API endpoints by path pattern (search-first for APIs).",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=lambda ctx, args: search_api_endpoints_tool.execute_search_api_endpoints(args, ctx.db, ctx.roots),
    ))


def _register_file_tools(reg: ToolRegistry):
    """파일 리스팅 및 읽기 관련 도구 등록"""
    reg.register(Tool(
        name="list_files",
        description="List indexed files with filters. If repo is omitted, returns repo summary only.",
        input_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path_pattern": {"type": "string"},
                "file_types": {"type": "array", "items": {"type": "string"}},
                "include_hidden": {"type": "boolean", "default": False},
                "summary": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
            },
        },
        handler=lambda ctx, args: list_files_tool.execute_list_files(args, ctx.db, ctx.logger, ctx.roots),
    ))

    reg.register(Tool(
        name="read_file",
        description="Read file content. DANGER: High token cost. Use ONLY after search/list_symbols. Prefer read_symbol or pagination (limit/offset) for large files.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=lambda ctx, args: read_file_tool.execute_read_file(args, ctx.db, ctx.roots),
    ))

    reg.register(Tool(
        name="index_file",
        description="Force immediate re-indexing for a file path. Use when content seems stale.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=lambda ctx, args: index_file_tool.execute_index_file(args, ctx.indexer, ctx.roots),
    ))

    reg.register(Tool(
        name="dry_run_diff",
        description="Preview diff and run lightweight syntax check before editing.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler=lambda ctx, args: dry_run_diff_tool.execute_dry_run_diff(args, ctx.db, ctx.roots),
    ))


def _register_symbol_tools(reg: ToolRegistry):
    """코드 구조 및 심볼 분석 관련 도구 등록"""
    reg.register(Tool(
        name="list_symbols",
        description="List all symbols in a file in a hierarchical tree. STRONGLY RECOMMENDED before read_file to understand structure with 90% fewer tokens.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file"},
            },
            "required": ["path"],
        },
        handler=lambda ctx, args: list_symbols_tool.execute_list_symbols(args, ctx.db, ctx.roots),
    ))

    reg.register(Tool(
        name="search_symbols",
        description="Search for symbols by name. Prefer this to scanning files.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "repo": {"type": "string"},
                "kinds": {"type": "array", "items": {"type": "string"}},
                "path_prefix": {"type": "string"},
                "match_mode": {"type": "string", "enum": ["contains", "prefix", "exact"], "default": "contains"},
                "include_qualname": {"type": "boolean", "default": True},
                "case_sensitive": {"type": "boolean", "default": False},
                "root_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
        handler=lambda ctx, args: search_symbols_tool.execute_search_symbols(args, ctx.db, ctx.logger, ctx.roots),
    ))

    reg.register(Tool(
        name="read_symbol",
        description="Read symbol definition block by name/path. Use after search_symbols.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Symbol name (recommended with path)"},
                "symbol_id": {"type": "string", "description": "Stable symbol id from search_symbols/call_graph"},
                "sid": {"type": "string", "description": "Alias for symbol_id"},
                "path": {"type": "string", "description": "Scoped path/root_id path to disambiguate duplicated names"},
                "limit": {"type": "integer", "default": 50},
            },
            "description": "Provide name+path or symbol_id/sid.",
        },
        handler=lambda ctx, args: read_symbol_tool.execute_read_symbol(args, ctx.db, ctx.logger, ctx.roots),
    ))

    reg.register(Tool(
        name="get_callers",
        description="Find callers of a symbol (use after search_symbols).",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Target symbol name"},
                "symbol_id": {"type": "string", "description": "Preferred when available"},
                "sid": {"type": "string", "description": "Alias for symbol_id"},
                "path": {"type": "string", "description": "Optional target path for disambiguation"},
                "repo": {"type": "string", "description": "Filter results by repository"},
                "limit": {"type": "integer", "default": 100},
            },
            "description": "Provide name or symbol_id/sid.",
        },
        handler=lambda ctx, args: get_callers_tool.execute_get_callers(args, ctx.db, ctx.roots),
    ))

    reg.register(Tool(
        name="get_implementations",
        description="Find implementations of a symbol (use after search_symbols).",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Interface/base type name"},
                "symbol_id": {"type": "string", "description": "Preferred when available"},
                "sid": {"type": "string", "description": "Alias for symbol_id"},
                "path": {"type": "string", "description": "Optional target path for disambiguation"},
                "repo": {"type": "string", "description": "Filter results by repository"},
                "limit": {"type": "integer", "default": 100},
            },
            "description": "Provide name or symbol_id/sid.",
        },
        handler=lambda ctx, args: get_implementations_tool.execute_get_implementations(args, ctx.db, ctx.roots),
    ))

    reg.register(Tool(
        name="call_graph",
        description="Call graph for a symbol (upstream/downstream).",
        input_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Target symbol name"},
                "name": {"type": "string", "description": "Alias for symbol"},
                "symbol_id": {"type": "string"},
                "sid": {"type": "string", "description": "Alias for symbol_id"},
                "path": {"type": "string"},
                "depth": {"type": "integer", "default": 2},
                "repo": {"type": "string", "description": "Filter results by repository"},
                "root_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional root_id scope"},
                "include_path": {"type": "array", "items": {"type": "string"}},
                "exclude_path": {"type": "array", "items": {"type": "string"}},
                "include_paths": {"type": "array", "items": {"type": "string"}},
                "exclude_paths": {"type": "array", "items": {"type": "string"}},
                "sort": {"type": "string", "enum": ["line", "name"], "default": "line"},
                "max_nodes": {"type": "integer", "default": 400},
                "max_edges": {"type": "integer", "default": 1200},
                "max_depth": {"type": "integer"},
                "max_time_ms": {"type": "integer", "default": 2000},
                "quality_score": {"type": "number"},
            },
            "description": "Provide symbol/name or symbol_id/sid.",
        },
        handler=lambda ctx, args: call_graph_tool.execute_call_graph(args, ctx.db, ctx.roots),
    ))

    reg.register(Tool(
        name="call_graph_health",
        description="Check call-graph plugin health and API compatibility.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda ctx, args: call_graph_health_tool.execute_call_graph_health(args, ctx.db, ctx.logger, ctx.roots),
    ))


def _register_knowledge_tools(reg: ToolRegistry):
    """지식베이스 및 스니펫 저장 관련 도구 등록"""
    reg.register(Tool(
        name="save_snippet",
        description="Save code snippet with a tag.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "tag": {"type": "string"},
                "note": {"type": "string"},
                "commit": {"type": "string"},
            },
            "required": ["path", "tag"],
        },
        handler=lambda ctx, args: save_snippet_tool.execute_save_snippet(args, ctx.db, ctx.roots, indexer=ctx.indexer),
    ))

    reg.register(Tool(
        name="get_snippet",
        description="Retrieve saved snippets by tag or query.",
        input_schema={
            "type": "object",
            "properties": {
                "tag": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=lambda ctx, args: get_snippet_tool.execute_get_snippet(args, ctx.db, ctx.roots),
    ))

    reg.register(Tool(
        name="archive_context",
        description="Archive domain knowledge/context.",
        input_schema={
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "related_files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["topic", "content"],
        },
        handler=lambda ctx, args: archive_context_tool.execute_archive_context(args, ctx.db, ctx.roots, indexer=ctx.indexer),
    ))

    reg.register(Tool(
        name="get_context",
        description="Retrieve archived context by topic or query.",
        input_schema={
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=lambda ctx, args: get_context_tool.execute_get_context(args, ctx.db, ctx.roots),
    ))
