import os
import importlib
from collections.abc import Mapping
from typing import TypeAlias

from ._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    invalid_args_response,
)
try:
    from .call_graph import PLUGIN_API_VERSION
except ImportError:
    PLUGIN_API_VERSION = 1

ToolResult: TypeAlias = dict[str, object]


def _load_plugins() -> list[str]:
    """환경 변수(SARI_CALLGRAPH_PLUGIN)로부터 로드할 호출 그래프 플러그인 목록을 읽어옵니다."""
    mod_path = os.environ.get("SARI_CALLGRAPH_PLUGIN", "").strip()
    if not mod_path:
        return []
    return [m.strip() for m in mod_path.split(",") if m.strip()]

def execute_call_graph_health(
    args: object,
    db: object,
    logger: object = None,
    roots: list[str] | None = None,
) -> ToolResult:
    """
    호출 그래프 플러그인의 상태와 API 호환성을 점검하는 도구입니다.
    로드된 플러그인들의 상태(loaded, error 등)와 버전을 확인합니다.
    """
    if not isinstance(args, Mapping):
        return invalid_args_response("call_graph_health", "args must be an object")

    def build_pack(payload: ToolResult) -> str:
        """PACK1 형식의 응답을 생성합니다."""
        header = pack_header("call_graph_health", {}, returned=1)
        lines = [header]
        for p in payload.get("plugins", []):
            lines.append(pack_line("p", {
                "name": pack_encode_id(p["name"]),
                "status": pack_encode_id(p["status"]),
                "version": str(p.get("version", 0))
            }))
        return "\n".join(lines)

    plugins = _load_plugins()
    results = []
    for p in plugins:
        try:
            # 플러그인 모듈 동적 로드 시도
            mod = importlib.import_module(p)
            results.append({"name": p, "status": "loaded", "version": getattr(mod, "VERSION", PLUGIN_API_VERSION)})
        except Exception as e:
            results.append({"name": p, "status": f"error: {str(e)}", "version": 0})

    payload = {"plugins": results, "api_version": PLUGIN_API_VERSION}
    return mcp_response("call_graph_health", lambda: build_pack(payload), lambda: payload)
