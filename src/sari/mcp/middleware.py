from typing import Callable, Optional, TypeAlias

ToolArgs: TypeAlias = dict[str, object]
ToolResult: TypeAlias = dict[str, object]
ToolContext: TypeAlias = object


class ToolMiddleware:
    def before(self, tool_name: str, ctx: ToolContext, args: ToolArgs) -> Optional[ToolResult]:
        return None

    def after(self, tool_name: str, ctx: ToolContext, result: ToolResult) -> ToolResult:
        return result

    def on_error(self, tool_name: str, ctx: ToolContext, error: ToolResult) -> ToolResult:
        return error


class PolicyMiddleware(ToolMiddleware):
    def __init__(self, policy_engine):
        self.policy_engine = policy_engine

    def before(self, tool_name: str, ctx: ToolContext, args: ToolArgs) -> Optional[ToolResult]:
        return self.policy_engine.check_pre_call(tool_name)

    def after(self, tool_name: str, ctx: ToolContext, result: ToolResult) -> ToolResult:
        return self.policy_engine.apply_post_call(tool_name, result)


def run_middlewares(
    tool_name: str,
    ctx: ToolContext,
    args: ToolArgs,
    middlewares: list[ToolMiddleware],
    execute_fn: Callable[[], object],
) -> ToolResult:
    for m in middlewares:
        res = m.before(tool_name, ctx, args)
        if res:
            return res
    try:
        result = execute_fn()
        if not isinstance(result, dict):
            raise TypeError("middleware execute_fn must return an object")
    except Exception as e:
        err = {"error": {"code": -32000, "message": str(e)}, "isError": True}
        for m in reversed(middlewares):
            err = m.on_error(tool_name, ctx, err)
        return err
    for m in reversed(middlewares):
        result = m.after(tool_name, ctx, result)
    return result
