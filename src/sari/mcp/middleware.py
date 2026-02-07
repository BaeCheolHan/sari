from typing import Any, Dict, Optional, List


class ToolMiddleware:
    def before(self, tool_name: str, ctx: Any, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return None

    def after(self, tool_name: str, ctx: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return result

    def on_error(self, tool_name: str, ctx: Any, error: Dict[str, Any]) -> Dict[str, Any]:
        return error


class PolicyMiddleware(ToolMiddleware):
    def __init__(self, policy_engine):
        self.policy_engine = policy_engine

    def before(self, tool_name: str, ctx: Any, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.policy_engine.check_pre_call(tool_name)

    def after(self, tool_name: str, ctx: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return self.policy_engine.apply_post_call(tool_name, result)


def run_middlewares(
    tool_name: str,
    ctx: Any,
    args: Dict[str, Any],
    middlewares: List[ToolMiddleware],
    execute_fn,
) -> Dict[str, Any]:
    for m in middlewares:
        res = m.before(tool_name, ctx, args)
        if res:
            return res
    try:
        result = execute_fn()
    except Exception as e:
        err = {"error": {"code": -32000, "message": str(e)}, "isError": True}
        for m in reversed(middlewares):
            err = m.on_error(tool_name, ctx, err)
        return err
    for m in reversed(middlewares):
        result = m.after(tool_name, ctx, result)
    return result
