import argparse
import json
import os
from pathlib import Path
from typing import List

from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.engine_registry import get_default_engine
from sari.core.workspace import WorkspaceManager


def _write_toml_block(cfg_path: Path, command: str, args: List[str], env: dict) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    lines = cfg_path.read_text(encoding="utf-8").splitlines() if cfg_path.exists() else []
    new_lines = []
    in_sari = False
    for line in lines:
        if line.strip() == "[mcp_servers.sari]":
            in_sari = True
            continue
        if in_sari and line.startswith("[") and line.strip() != "[mcp_servers.sari]":
            in_sari = False
            new_lines.append(line)
            continue
        if not in_sari:
            new_lines.append(line)
    env_kv = ", ".join([f'{k} = "{v}"' for k, v in env.items()])
    block = [
        "[mcp_servers.sari]",
        f'command = "{command}"',
        f"args = {json.dumps(args)}",
        f"env = {{ {env_kv} }}",
        "startup_timeout_sec = 60",
    ]
    new_lines = block + new_lines
    cfg_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _write_json_settings(cfg_path: Path, command: str, args: List[str], env: dict) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    mcp_servers = data.get("mcpServers") or {}
    mcp_servers["sari"] = {"command": command, "args": args, "env": env}
    data["mcpServers"] = mcp_servers
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cmd_install(host: str, do_print: bool) -> int:
    args = ["--transport", "stdio", "--format", "pack"]
    command = "sari"
    env = {}

    if do_print:
        payload = {
            "command": command,
            "args": args,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if host in {"codex", "gemini"}:
        cfg_path = Path.cwd() / f".{host}" / "config.toml"
        _write_toml_block(cfg_path, command, args, env)
        print(f"[sari] Updated {cfg_path}")
        return 0
    if host in {"claude"}:
        if os.name == "nt":
            cfg_path = Path(os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))) / "Claude" / "claude_desktop_config.json"
        else:
            cfg_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        _write_json_settings(cfg_path, command, args, env)
        print(f"[sari] Updated {cfg_path}")
        return 0
    if host in {"cursor"}:
        cfg_path = Path.home() / ".cursor" / "mcp.json"
        _write_json_settings(cfg_path, command, args, env)
        print(f"[sari] Updated {cfg_path}")
        return 0

    print(f"[sari] Unsupported host: {host}", file=os.sys.stderr)
    return 2


def _cmd_config_show() -> int:
    cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
    if not Path(cfg_path).exists():
        print("{}")
        return 0
    print(Path(cfg_path).read_text(encoding="utf-8"))
    return 0


def _cmd_roots_list() -> int:
    cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
    if not Path(cfg_path).exists():
        print("[]")
        return 0
    data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    roots = data.get("roots") or data.get("workspace_roots") or []
    print(json.dumps(roots, ensure_ascii=False, indent=2))
    return 0


def _cmd_roots_add(path: str) -> int:
    candidate = WorkspaceManager.normalize_path(path)
    p = Path(candidate).expanduser()
    normalized = WorkspaceManager.normalize_path(str(p))
    if not p.exists() or not p.is_dir():
        print(f"Root path does not exist: {normalized}", file=os.sys.stderr)
        return 2

    cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
    data = {}
    if Path(cfg_path).exists():
        try:
            data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
        except Exception:
            data = {}
    roots = data.get("roots") or data.get("workspace_roots") or []
    roots = [
        WorkspaceManager.normalize_path(str(Path(str(r)).expanduser()))
        for r in roots
        if r
    ]
    roots.append(normalized)
    final = list(dict.fromkeys(roots))
    data["roots"] = final
    Path(cfg_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg_path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


def _cmd_roots_remove(path: str) -> int:
    cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
    if not Path(cfg_path).exists():
        print("[]")
        return 0
    data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    roots = data.get("roots") or data.get("workspace_roots") or []
    roots = [r for r in roots if r and r != path]
    data["roots"] = roots
    Path(cfg_path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(roots, ensure_ascii=False, indent=2))
    return 0


def _cmd_index() -> int:
    try:
        from sari.mcp.cli import _request_http
        _request_http("/rescan", {})
        print(json.dumps({"requested": True}))
        return 0
    except Exception as e:
        print(json.dumps({"requested": False, "error": str(e)}))
        return 1


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


def _cmd_doctor() -> int:
    try:
        from sari.mcp.tools.doctor import execute_doctor
        from urllib.parse import unquote

        res = execute_doctor({})

        if isinstance(res, dict) and "content" in res:
            content = res["content"][0]["text"]
            try:
                data = json.loads(content)
                print(json.dumps(data, ensure_ascii=False, indent=2))
                return 0
            except Exception:
                pass

            if content.startswith("PACK1"):
                lines = content.splitlines()
                for line in lines:
                    if line.startswith("t:"):
                        encoded_val = line[2:]
                        decoded_val = unquote(encoded_val)
                        try:
                            data = json.loads(decoded_val)
                            print(json.dumps(data, ensure_ascii=False, indent=2))
                            return 0
                        except Exception:
                            print(decoded_val)
                            return 0

            print(content)
        return 0
    except Exception as e:
        print(f"Doctor failed: {e}", file=os.sys.stderr)
        return 1


def run_cmd(argv: List[str]) -> int:
    if not argv:
        print("missing subcommand", file=os.sys.stderr)
        return 2
    if argv[0] in {"status", "search"}:
        from sari.mcp.cli import main as mcp_cli_main
        return mcp_cli_main(argv)
    if argv[0] == "doctor":
        return _cmd_doctor()
    if argv[0] == "config" and len(argv) > 1 and argv[1] == "show":
        return _cmd_config_show()
    if argv[0] == "roots":
        if len(argv) < 2:
            print("roots add|remove|list", file=os.sys.stderr)
            return 2
        if argv[1] == "list":
            return _cmd_roots_list()
        if argv[1] == "add" and len(argv) > 2:
            return _cmd_roots_add(argv[2])
        if argv[1] == "remove" and len(argv) > 2:
            return _cmd_roots_remove(argv[2])
    if argv[0] == "index":
        return _cmd_index()
    if argv[0] == "install":
        parser = argparse.ArgumentParser(prog="sari --cmd install")
        parser.add_argument("--host", required=True, choices=["codex", "gemini", "claude", "cursor"])
        parser.add_argument("--print", action="store_true")
        ns = parser.parse_args(argv[1:])
        return _cmd_install(ns.host, ns.print)
    if argv[0] == "engine":
        if len(argv) < 2:
            print("engine status|install|rebuild|verify", file=os.sys.stderr)
            return 2
        action = argv[1]
        if action == "status":
            return _cmd_engine_status()
        if action == "install":
            return _cmd_engine_install()
        if action == "rebuild":
            return _cmd_engine_rebuild()
        if action == "verify":
            return _cmd_engine_verify()
    if argv[0] == "uninstall":
        from sari import uninstall as uninstall_mod
        return uninstall_mod.main(argv[1:])
    print(f"Unknown subcommand: {argv[0]}", file=os.sys.stderr)
    return 2

