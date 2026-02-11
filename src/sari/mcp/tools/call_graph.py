from collections.abc import Mapping
from typing import TypeAlias

from ._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    ErrorCode,
    invalid_args_response,
)
from sari.core.services.call_graph.service import CallGraphService

ToolResult: TypeAlias = dict[str, object]


def build_call_graph(args: Mapping[str, object], db: object, roots: list[str]) -> ToolResult:
    """레거시 진입점이며, 내부적으로 CallGraphService를 사용하여 그래프를 생성합니다."""
    svc = CallGraphService(db, roots)
    return svc.build(args)

def execute_call_graph(
    args: object,
    db: object,
    logger: object = None,
    roots: list[str] | None = None,
) -> ToolResult:
    """
    특정 심볼의 호출 그래프(Call Graph)를 생성하는 도구입니다.
    심볼의 상위(Upstream) 및 하위(Downstream) 호출 관계를 계층 구조로 시각화합니다.
    """
    if roots is None and isinstance(logger, list):
        roots, logger = logger, None
    if not isinstance(args, Mapping):
        return invalid_args_response("call_graph", "args must be an object")
    
    def build_pack(payload: ToolResult) -> str:
        """PACK1 형식의 응답을 생성합니다."""
        d = str(int(args.get("depth") or 2))
        header = pack_header("call_graph", {
            "symbol": pack_encode_text(payload.get("symbol", "")),
            "depth": d,
            "quality": pack_encode_id(payload.get("graph_quality", "")),
            "truncated": str(bool(payload.get("truncated"))).lower(),
        }, returned=1)
        meta = payload.get("meta", {})
        lines = [
            header,
            "t:" + pack_encode_text(payload.get("tree", "")),
            pack_line("m", {"scope_reason": pack_encode_text(payload.get("scope_reason", ""))}),
            pack_line("m", {"nodes": str(meta.get("nodes", 0)), "edges": str(meta.get("edges", 0))}),
        ]
        return "\n".join(lines)

    try:
        # CallGraphService를 통한 그래프 빌드
        svc = CallGraphService(db, roots or [])
        payload = svc.build(args)
        return mcp_response("call_graph", lambda: build_pack(payload), lambda: payload)
    except Exception as e:
        import traceback
        stack = traceback.format_exc()
        msg = str(e)
        code = ErrorCode.DB_ERROR if "db" in msg.lower() else ErrorCode.INVALID_ARGS
        return mcp_response(
            "call_graph",
            lambda: pack_error("call_graph", code, f"{msg}: {stack}"),
            lambda: {"error": {"code": code.value, "message": msg}, "isError": True},
        )
