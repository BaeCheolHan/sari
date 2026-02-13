from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.engine_registry import get_default_engine
from sari.entry_command_context import CommandContext


def _load_engine_context(ctx: CommandContext | None = None):
    ctx = ctx or CommandContext()
    workspace_root = ctx.resolve_workspace_root()
    cfg_path = ctx.resolve_config_path()
    cfg = Config.load(cfg_path, workspace_root_override=workspace_root)
    db = LocalSearchDB(cfg.db_path)
    db.set_engine(get_default_engine(db, cfg, cfg.workspace_roots))
    return cfg, db


def _cmd_engine_status(ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    try:
        _cfg, db = _load_engine_context(ctx)
        if hasattr(db.engine, "status"):
            st = db.engine.status()
            ctx.print_json(st.__dict__)
            return 0
        ctx.print_json({"error": "engine status unsupported"})
        return 1
    except Exception as e:
        ctx.print_json({"error": str(e)})
        return 1


def _cmd_engine_install(ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    try:
        _cfg, db = _load_engine_context(ctx)
        if hasattr(db.engine, "install"):
            db.engine.install()
            ctx.print_json({"ok": True})
            return 0
        ctx.print_json({"error": "engine install unsupported"})
        return 1
    except Exception as e:
        ctx.print_json({"error": str(e)})
        return 1


def _cmd_engine_rebuild(ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    try:
        _cfg, db = _load_engine_context(ctx)
        if hasattr(db.engine, "rebuild"):
            db.engine.rebuild()
            ctx.print_json({"ok": True})
            return 0
        ctx.print_json({"error": "engine rebuild unsupported"})
        return 1
    except Exception as e:
        ctx.print_json({"error": str(e)})
        return 1


def _cmd_engine_verify(ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    try:
        _cfg, db = _load_engine_context(ctx)
        if hasattr(db.engine, "status"):
            st = db.engine.status()
            if st.engine_ready:
                ctx.print_json({"ok": True})
                return 0
            ctx.print_json({"ok": False, "reason": st.reason, "hint": st.hint})
            return 2
        ctx.print_json({"error": "engine status unsupported"})
        return 1
    except Exception as e:
        ctx.print_json({"error": str(e)})
        return 1
