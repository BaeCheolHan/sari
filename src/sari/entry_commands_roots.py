import json
from pathlib import Path

from sari.entry_command_context import CommandContext


def _cmd_config_show(ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    cfg_path = ctx.resolve_config_path()
    if not Path(cfg_path).exists():
        ctx.print_line("{}")
        return 0
    ctx.print_line(Path(cfg_path).read_text(encoding="utf-8"))
    return 0


def _cmd_roots_list(ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    cfg_path = ctx.resolve_config_path()
    if not Path(cfg_path).exists():
        ctx.print_line("[]")
        return 0
    data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    roots = data.get("roots") or data.get("workspace_roots") or []
    ctx.print_json(roots)
    return 0


def _cmd_roots_add(path: str, ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    normalized = ctx.normalize_existing_dir(path)
    if not normalized:
        candidate = ctx.normalize_path(path)
        normalized = ctx.normalize_path(str(Path(candidate).expanduser()))
        ctx.print_err(f"Root path does not exist: {normalized}")
        return 2

    cfg_path = ctx.resolve_config_path()
    data = {}
    if Path(cfg_path).exists():
        try:
            data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
        except Exception:
            data = {}
    roots = data.get("roots") or data.get("workspace_roots") or []
    roots = [
        ctx.normalize_path(str(Path(str(r)).expanduser()))
        for r in roots
        if r
    ]
    roots.append(normalized)
    final = list(dict.fromkeys(roots))
    data["roots"] = final
    Path(cfg_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg_path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ctx.print_json(final)
    return 0


def _cmd_roots_remove(path: str, ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    cfg_path = ctx.resolve_config_path()
    if not Path(cfg_path).exists():
        ctx.print_line("[]")
        return 0
    data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    roots = data.get("roots") or data.get("workspace_roots") or []
    roots = [r for r in roots if r and r != path]
    data["roots"] = roots
    Path(cfg_path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ctx.print_json(roots)
    return 0
