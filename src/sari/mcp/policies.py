import time
from typing import TypeAlias
from sari.mcp.tools._util import mcp_response, pack_error, ErrorCode

ToolResult: TypeAlias = dict[str, object]
UsageState: TypeAlias = dict[str, object]


class PolicyEngine:
    def __init__(self, mode: str = "warn"):
        self.mode = mode
        self.usage: UsageState = {
            "search": 0,
            "search_symbols": 0,
            "last_search_ts": None,
            "last_search_symbols_ts": None,
            "read_without_search": 0,
        }

    def mark_action(self, tool_name: str):
        now = time.time()
        if tool_name in {"search", "grep_and_read"}:
            self.usage["search"] += 1
            self.usage["last_search_ts"] = now
        elif tool_name == "search_symbols":
            self.usage["search_symbols"] += 1
            self.usage["last_search_symbols_ts"] = now

    def has_search_context(self) -> bool:
        search_count = int(self.usage.get("search", 0) or 0)
        symbol_search_count = int(self.usage.get("search_symbols", 0) or 0)
        return search_count > 0 or symbol_search_count > 0

    def check_pre_call(self, tool_name: str) -> ToolResult | None:
        if self.mode == "off":
            return None
            
        if tool_name in {"read_file", "read_symbol"}:
            if not self.has_search_context():
                self.usage["read_without_search"] += 1
                if self.mode == "enforce":
                    return mcp_response(
                        "search_first",
                        lambda: pack_error("search_first", ErrorCode.INVALID_ARGS, "search-first policy active. Call search/search_symbols before read_file/read_symbol."),
                        lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "search-first policy active. Call search/search_symbols before read_file/read_symbol."}, "isError": True},
                    )
        return None

    def apply_post_call(self, tool_name: str, result: ToolResult) -> ToolResult:
        if self.mode == "warn" and tool_name in {"read_file", "read_symbol"}:
            if not self.has_search_context():
                raw_warnings = result.get("warnings", [])
                warnings = list(raw_warnings) if isinstance(raw_warnings, list) else []
                if "Search-first policy (advisory): call search/search_symbols before read_file/read_symbol." not in warnings:
                    warnings.append("Search-first policy (advisory): call search/search_symbols before read_file/read_symbol.")
                result["warnings"] = warnings
        return result
