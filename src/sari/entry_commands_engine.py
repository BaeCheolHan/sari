import json
from pathlib import Path

from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.engine_registry import get_default_engine
from sari.core.workspace import WorkspaceManager


def _load_engine_context():
    workspace_root = WorkspaceManager.resolve_workspace_root()
    cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
    cfg = Config.load(cfg_path, workspace_root_override=workspace_root)
    db = LocalSearchDB(cfg.db_path)
    db.set_engine(get_default_engine(db, cfg, cfg.workspace_roots))
    return cfg, db


def _cmd_engine_status() -> int:
    try:
        _cfg, db = _load_engine_context()
        if hasattr(db.engine, "status"):
            st = db.engine.status()
            print(json.dumps(st.__dict__, ensure_ascii=False, indent=2))
            return 0
        print(json.dumps({"error": "engine status unsupported"}, ensure_ascii=False, indent=2))
        return 1
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def _cmd_engine_install() -> int:
    try:
        _cfg, db = _load_engine_context()
        if hasattr(db.engine, "install"):
            db.engine.install()
            print(json.dumps({"ok": True}))
            return 0
        print(json.dumps({"error": "engine install unsupported"}))
        return 1
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def _cmd_engine_rebuild() -> int:
    try:
        _cfg, db = _load_engine_context()
        if hasattr(db.engine, "rebuild"):
            db.engine.rebuild()
            print(json.dumps({"ok": True}))
            return 0
        print(json.dumps({"error": "engine rebuild unsupported"}))
        return 1
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def _cmd_engine_verify() -> int:
    try:
        _cfg, db = _load_engine_context()
        if hasattr(db.engine, "status"):
            st = db.engine.status()
            if st.engine_ready:
                print(json.dumps({"ok": True}))
                return 0
            print(json.dumps({"ok": False, "reason": st.reason, "hint": st.hint}))
            return 2
        print(json.dumps({"error": "engine status unsupported"}))
        return 1
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1

