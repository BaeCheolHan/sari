import json
import os
from pathlib import Path
from typing import List

from sari.entry_command_context import CommandContext


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


def _cmd_install(host: str, do_print: bool, ctx: CommandContext | None = None) -> int:
    ctx = ctx or CommandContext()
    args = ["--transport", "stdio", "--format", "pack"]
    command = "sari"
    env = {}

    if do_print:
        payload = {
            "command": command,
            "args": args,
        }
        ctx.print_json(payload)
        return 0

    if host in {"codex", "gemini"}:
        cfg_path = Path(ctx.cwd) / f".{host}" / "config.toml"
        _write_toml_block(cfg_path, command, args, env)
        ctx.print_line(f"[sari] Updated {cfg_path}")
        return 0
    if host in {"claude"}:
        if os.name == "nt":
            cfg_path = Path(os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))) / "Claude" / "claude_desktop_config.json"
        else:
            cfg_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        _write_json_settings(cfg_path, command, args, env)
        ctx.print_line(f"[sari] Updated {cfg_path}")
        return 0
    if host in {"cursor"}:
        cfg_path = Path.home() / ".cursor" / "mcp.json"
        _write_json_settings(cfg_path, command, args, env)
        ctx.print_line(f"[sari] Updated {cfg_path}")
        return 0

    ctx.print_err(f"[sari] Unsupported host: {host}")
    return 2
