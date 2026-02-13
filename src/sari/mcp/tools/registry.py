import os
from dataclasses import dataclass
from typing import Callable, Optional

import sari.mcp.tools.search as search_tool
import sari.mcp.tools.status as status_tool
import sari.mcp.tools.list_files as list_files_tool
import sari.mcp.tools.list_symbols as list_symbols_tool
import sari.mcp.tools.doctor as doctor_tool
import sari.mcp.tools.index_file as index_file_tool
import sari.mcp.tools.rescan as rescan_tool
import sari.mcp.tools.scan_once as scan_once_tool
import sari.mcp.tools.get_callers as get_callers_tool
import sari.mcp.tools.get_implementations as get_implementations_tool
import sari.mcp.tools.guide as guide_tool
import sari.mcp.tools.call_graph as call_graph_tool
import sari.mcp.tools.call_graph_health as call_graph_health_tool
import sari.mcp.tools.knowledge as knowledge_tool
import sari.mcp.tools.read as read_tool
from sari.mcp.tools._util import ErrorCode, mcp_response, pack_error


ToolInputSchema = dict[str, object]
ToolArgs = dict[str, object]
ToolResult = dict[str, object]
ToolHandler = Callable[["ToolContext", ToolArgs], ToolResult]


@dataclass
class Tool:
    """
    MCP 도구 정의 데이터 클래스.
    이름, 설명, 입력 스키마 및 실행 핸들러 정보를 포함합니다.
    """
    name: str
    description: str
    input_schema: ToolInputSchema
    handler: ToolHandler
    hidden: bool = False
    deprecated: bool = False


@dataclass
class ToolContext:
    """
    도구 실행 시 제공되는 컨텍스트 정보.
    DB 접근 주체, 인덱서, 설정 및 환경 정보를 캡슐화합니다.
    """
    db: object
    engine: object
    indexer: object
    roots: list[str]
    cfg: object
    logger: object
    workspace_root: str
    server_version: str
    policy_engine: Optional[object] = None  # 정책 추적용 추가 필드


class ToolRegistry:
    """
    Sari MCP 도구들을 등록하고 관리하며 실행을 위임하는 레지스트리 클래스입니다.
    """
    
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """도구를 레지스트리에 등록합니다."""
        self._tools[tool.name] = tool

    def list_tools_raw(self) -> list[Tool]:
        """등록된 원본 도구 객체 목록을 반환합니다."""
        return list(self._tools.values())

    def list_tools(self) -> list[dict[str, object]]:
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
                **({"deprecated": True} if t.deprecated else {}),
            }
            for t in self._tools.values()
            if (not t.hidden) or expose_internal
        ]

    def execute(self, name: str, ctx: ToolContext, args: ToolArgs) -> ToolResult:
        """
        요청된 도구를 찾아 실행하고 결과를 반환합니다.
        정책 엔진(Policy Engine)이 활성화된 경우 실행 이력을 마킹합니다.
        """
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
            
        result = tool.handler(ctx, args)

        def _is_error_response(res: ToolResult) -> bool:
            if bool(res.get("isError")):
                return True
            try:
                content = res.get("content") or []
                if not isinstance(content, list) or not content:
                    return False
                first_item = content[0] if content else {}
                if not isinstance(first_item, dict):
                    return False
                text = str(first_item.get("text") or "")
                if not text:
                    return False
                first_line = text.splitlines()[0].strip()
                if first_line.startswith("PACK1 ") and " ok=false" in first_line:
                    return True
            except Exception:
                return False
            return False

        # 실행 정책 훅: 검색 액션 자동 마킹
        if ctx.policy_engine and not _is_error_response(result):
            if name in {"search"}:
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
        hidden=True,
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
        hidden=True,
    ))


def _register_search_tools(reg: ToolRegistry):
    """검색 및 탐색 관련 도구 등록 (통합 검색 v3)"""
    reg.register(Tool(
        name="search",
        description="SEARCH FIRST. MANDATORY before reading files. Use to locate relevant paths/symbols. Prevents token waste by narrowing scope.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어"},
                "search_type": {
                    "type": "string",
                    "enum": ["code", "symbol", "api", "repo", "auto"],
                    "default": "code",
                    "description": "검색 대상. auto는 내부 추론 + waterfall 실행",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                    "description": "반환 최대 개수",
                },
                "path_pattern": {"type": "string", "description": "경로 필터 (Only for code/api)"},
                "file_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "확장자 필터 (Only for code/api)",
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "심볼 종류 필터 (Only for symbol)",
                },
                "match_mode": {
                    "type": "string",
                    "enum": ["exact", "prefix", "fuzzy"],
                    "default": "fuzzy",
                    "description": "심볼 매칭 방식 (Only for symbol)",
                },
                "include_qualname": {
                    "type": "boolean",
                    "default": True,
                    "description": "qualname 포함 여부 (Only for symbol)",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "description": "HTTP method 필터 (Only for api)",
                },
                "framework_hint": {"type": "string", "description": "프레임워크 힌트 (Only for api)"},
                "preview_mode": {
                    "type": "string",
                    "enum": ["none", "snippet"],
                    "default": "snippet",
                    "description": "미리보기 모드 (Only for code/symbol/api)",
                },
                "context_lines": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 20,
                    "default": 3,
                    "description": "스니펫 문맥 라인 수 (Only for preview_mode='snippet')",
                },
                "max_preview_chars": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 4000,
                    "default": 1200,
                    "description": "스니펫 최대 길이",
                },
                "fallback_repo_suggestions": {
                    "type": "boolean",
                    "default": True,
                    "description": "결과 0건일 때 repo 추천 첨부",
                },
            },
            "required": ["query"],
        },
        handler=lambda ctx, args: search_tool.execute_search(args, ctx.db, ctx.logger, ctx.roots, engine=ctx.engine, indexer=ctx.indexer),
    ))


def _register_file_tools(reg: ToolRegistry):
    """파일 리스팅 및 읽기 관련 도구 등록"""
    reg.register(Tool(
        name="read",
        description="Unified read interface for file/symbol/snippet/diff preview/ast_edit modes.",
        input_schema={
            "type": "object",
            "description": (
                "Unified read. Mode-specific usage: "
                "file->target(+offset/limit), "
                "symbol->target(+path/include_context), "
                "snippet->target(+start_line/end_line/context_lines), "
                "diff_preview->target(+against=HEAD|WORKTREE|INDEX), "
                "ast_edit->target(+expected_version_hash/+old_text/+new_text)."
            ),
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["file", "symbol", "snippet", "diff_preview", "ast_edit"],
                    "description": "Read mode selector.",
                },
                "target": {"type": "string", "description": "Primary lookup target (path/symbol/snippet selector)."},
                "path": {"type": "string", "description": "Disambiguation path for symbol mode."},
                "name": {"type": "string", "description": "Symbol name for symbol mode."},
                "symbol_id": {"type": "string", "description": "Stable symbol id for symbol mode."},
                "sid": {"type": "string", "description": "Alias for symbol_id."},
                "tag": {"type": "string", "description": "Snippet tag for snippet mode."},
                "query": {"type": "string", "description": "Free-form query for snippet mode."},
                "content": {"type": "string", "description": "Proposed content for diff_preview mode."},
                "against": {
                    "type": "string",
                    "enum": ["HEAD", "WORKTREE", "INDEX"],
                    "description": "Baseline for mode=diff_preview only.",
                },
                "start_line": {"type": "integer", "description": "Snippet start line (snippet mode only)."},
                "end_line": {"type": "integer", "description": "Snippet end line (snippet mode only)."},
                "context_lines": {"type": "integer", "description": "Snippet context lines (snippet mode only)."},
                "include_context": {"type": "boolean", "description": "Include symbol context (symbol mode only)."},
                "preview_mode": {
                    "type": "string",
                    "enum": ["none", "snippet"],
                    "description": "Preview rendering strategy.",
                },
                "max_preview_chars": {"type": "integer", "description": "Hard cap for preview payload size."},
                "offset": {"type": "integer", "description": "Line offset for paginated reads."},
                "limit": {"type": "integer", "description": "Maximum items/lines to return."},
                "expected_version_hash": {"type": "string", "description": "Required optimistic-lock hash for mode=ast_edit."},
                "old_text": {"type": "string", "description": "Text to replace once for mode=ast_edit."},
                "new_text": {"type": "string", "description": "Replacement text for mode=ast_edit."},
                "symbol": {"type": "string", "description": "Symbol name for block replacement in mode=ast_edit (Python/JS or tree-sitter-backed languages)."},
                "symbol_qualname": {"type": "string", "description": "Qualified symbol hint (e.g. Class.method) for tree-sitter disambiguation in mode=ast_edit."},
                "symbol_kind": {
                    "type": "string",
                    "enum": ["function", "method", "class", "interface", "struct", "trait", "enum", "module"],
                    "description": "Symbol kind hint for tree-sitter disambiguation in mode=ast_edit.",
                },
            },
            "required": ["mode"],
        },
        handler=lambda ctx, args: read_tool.execute_read(
            {**(args if isinstance(args, dict) else {}), "__indexer__": ctx.indexer},
            ctx.db,
            ctx.roots,
            ctx.logger,
        ),
    ))

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
        description="DEPRECATED legacy wrapper. Use unified `read` with mode=file.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=lambda ctx, args: read_tool.execute_read(
            {
                "mode": "file",
                "target": args.get("path"),
                **({"offset": args.get("offset")} if "offset" in args else {}),
                **({"limit": args.get("limit")} if "limit" in args else {}),
            },
            ctx.db,
            ctx.roots,
            ctx.logger,
        ),
        hidden=True,
        deprecated=True,
    ))

    reg.register(Tool(
        name="index_file",
        description="Force immediate re-indexing for a file path. Use when content seems stale.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=lambda ctx, args: index_file_tool.execute_index_file(args, ctx.indexer, ctx.roots),
    ))

    reg.register(Tool(
        name="dry_run_diff",
        description="DEPRECATED legacy wrapper. Use unified `read` with mode=diff_preview.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler=lambda ctx, args: read_tool.execute_read(
            {
                "mode": "diff_preview",
                "target": args.get("path"),
                "content": args.get("content"),
                **({"against": args.get("against")} if "against" in args else {}),
            },
            ctx.db,
            ctx.roots,
            ctx.logger,
        ),
        hidden=True,
        deprecated=True,
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
        name="read_symbol",
        description="DEPRECATED legacy wrapper. Use unified `read` with mode=symbol.",
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
        handler=lambda ctx, args: read_tool.execute_read(
            {
                "mode": "symbol",
                "target": args.get("name") or args.get("symbol_id") or args.get("sid"),
                **({"name": args.get("name")} if "name" in args else {}),
                **({"symbol_id": args.get("symbol_id")} if "symbol_id" in args else {}),
                **({"sid": args.get("sid")} if "sid" in args else {}),
                **({"path": args.get("path")} if "path" in args else {}),
                **({"limit": args.get("limit")} if "limit" in args else {}),
            },
            ctx.db,
            ctx.roots,
            ctx.logger,
        ),
        hidden=True,
        deprecated=True,
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
        hidden=True,
    ))


def _register_knowledge_tools(reg: ToolRegistry):
    """지식베이스 및 스니펫 저장 관련 도구 등록"""
    def _legacy_knowledge_block(tool_name: str, guidance: str):
        def _handler(_ctx: ToolContext, args: ToolArgs) -> ToolResult:
            if bool((args or {}).get("__internal__")):
                return {"content": [{"type": "text", "text": "PACK1 tool=legacy ok=true"}]}
            msg = f"Legacy tool '{tool_name}' is blocked. {guidance}"
            return mcp_response(
                tool_name,
                lambda: pack_error(tool_name, ErrorCode.INVALID_ARGS, msg),
                lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": msg}, "isError": True},
            )

        return _handler

    reg.register(Tool(
        name="knowledge",
        description="Unified knowledge interface. Use action=save|recall|delete|list|relink.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "recall", "search", "delete", "list", "relink"]},
                "type": {"type": "string", "enum": ["context", "snippet"]},
                "key": {"type": "string"},
                "query": {"type": "string"},
                "content": {"type": "string"},
                "context_ref": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["action"],
        },
        handler=lambda ctx, args: knowledge_tool.execute_knowledge(args, ctx.db, ctx.roots, indexer=ctx.indexer),
    ))

    reg.register(Tool(
        name="save_snippet",
        description="Legacy tool. Use `knowledge` with action=save,type=snippet.",
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
        handler=_legacy_knowledge_block("save_snippet", "Use knowledge(action='save', type='snippet')."),
        hidden=True,
        deprecated=True,
    ))

    reg.register(Tool(
        name="get_snippet",
        description="Legacy tool. Use `knowledge` with action=recall,type=snippet.",
        input_schema={
            "type": "object",
            "properties": {
                "tag": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=_legacy_knowledge_block("get_snippet", "Use knowledge(action='recall', type='snippet')."),
        hidden=True,
        deprecated=True,
    ))

    reg.register(Tool(
        name="archive_context",
        description="Legacy tool. Use `knowledge` with action=save,type=context.",
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
        handler=_legacy_knowledge_block("archive_context", "Use knowledge(action='save', type='context')."),
        hidden=True,
        deprecated=True,
    ))

    reg.register(Tool(
        name="get_context",
        description="Legacy tool. Use `knowledge` with action=recall,type=context.",
        input_schema={
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=_legacy_knowledge_block("get_context", "Use knowledge(action='recall', type='context')."),
        hidden=True,
        deprecated=True,
    ))
