from sari.entry_command_context import CommandContext


def _cmd_index(ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    try:
        from sari.mcp.cli import _request_http

        _request_http("/rescan", {})
        ctx.print_json({"requested": True})
        return 0
    except Exception as e:
        ctx.print_json({"requested": False, "error": str(e)})
        return 1
