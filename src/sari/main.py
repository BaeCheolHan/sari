# ruff: noqa: E402
# CRITICAL: Install stdout guard at entry point to protect MCP protocol
# This prevents third-party library noise from breaking JSON-RPC communication
from sari.mcp.stdout_guard import install_guard, get_real_stdout

# Initialize structured logging early
from sari.core.utils.logging import configure_logging

import argparse
import os
import sys
import threading


import subprocess
from pathlib import Path
from typing import List

from sari.core.workspace import WorkspaceManager
from sari.core.config import validate_config_file
from sari.entry_bootstrap import dispatch_pre_stdio, parse_transport_args
from sari.entry_commands import run_cmd
from sari.entry_commands_roots import _cmd_roots_add

_RUNTIME_BOOTSTRAPPED = False


def _bootstrap_runtime() -> None:
    global _RUNTIME_BOOTSTRAPPED
    if _RUNTIME_BOOTSTRAPPED:
        return
    install_guard()
    configure_logging()
    _RUNTIME_BOOTSTRAPPED = True


def _should_http_daemon(ns: argparse.Namespace) -> bool:
    if ns.http_daemon:
        return True
    env = (os.environ.get("SARI_HTTP_DAEMON") or "").strip().lower()
    return env in {"1", "true", "yes", "on"}


def _run_http_server() -> int:
    from sari.core.main import main as http_main
    return http_main()


def _set_http_api_port(port: str) -> None:
    if port:
        os.environ["SARI_HTTP_API_PORT"] = str(port)


def _spawn_http_daemon(ns: argparse.Namespace) -> int:
    def _reap_child(proc: subprocess.Popen) -> None:
        try:
            proc.wait()
        except Exception:
            pass

    if os.environ.get("SARI_HTTP_DAEMON_CHILD"):
        return _run_http_server()
    env = os.environ.copy()
    env["SARI_HTTP_DAEMON_CHILD"] = "1"
    cmd = [sys.executable, "-m", "sari", "--transport", "http"]
    if ns.http_api_port:
        cmd += ["--http-api-port", str(ns.http_api_port)]
    debug = os.environ.get("SARI_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    log_path = None
    log_fh = None
    try:
        if os.name == "nt":
            base_dir = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())) / "sari"
        else:
            base_dir = Path.home() / ".local" / "share" / "sari"
        base_dir.mkdir(parents=True, exist_ok=True)
        log_path = base_dir / "http-daemon.log"
        log_fh = open(log_path, "a", encoding="utf-8")
        stdout_target = log_fh
        stderr_target = None if debug else log_fh
    except Exception:
        stdout_target = subprocess.DEVNULL
        stderr_target = None if debug else subprocess.DEVNULL
    proc = subprocess.Popen(
        cmd,
        stdout=stdout_target,
        stderr=stderr_target,
        start_new_session=True,
        env=env,
    )
    if log_fh:
        try:
            log_fh.close()
        except Exception:
            pass
    # Reap child on exit to avoid defunct processes in long-lived MCP stdio hosts.
    threading.Thread(target=_reap_child, args=(proc,), daemon=True).start()
    port_note = ns.http_api_port or os.environ.get("SARI_HTTP_API_PORT") or "default"
    log_note = f", log: {log_path}" if log_path else ""
    print(f"[sari] HTTP daemon started in background (port: {port_note}{log_note})", file=sys.stderr)
    return 0


def _parse_transport_args(argv: List[str]) -> argparse.Namespace:
    return parse_transport_args(argv)


def _dispatch_pre_stdio(argv: List[str]) -> int | None:
    return dispatch_pre_stdio(argv, run_cmd_fn=run_cmd)


def main(argv: List[str] | None = None, original_stdout: object | None = None) -> int:
    _bootstrap_runtime()

    # Ensure global config exists before doing anything else
    WorkspaceManager.ensure_global_config()

    argv = list(argv or sys.argv[1:])

    # Use provided stdout or fallback to get_real_stdout() to bypass StdoutGuard
    # This ensures the MCP server writes to the actual stdout even if guarded.
    clean_stdout = original_stdout or get_real_stdout()

    # Feature flag: Async MCP Server
    # Enable via SARI_ASYNC_MCP=1 or --async-mcp flag (handled below)
    use_async = os.environ.get("SARI_ASYNC_MCP", "").strip().lower() in {"1", "true", "yes", "on"}

    routed = _dispatch_pre_stdio(argv)
    if routed is not None:
        return routed

    ns = _parse_transport_args(argv)

    if ns.help:
        print("sari [--transport stdio|http] [--format pack|json] [--http-api] [--http-api-port PORT] [--http-daemon] [--cmd <subcommand>]")
        return 0
    if ns.version:
        from sari.mcp.server import LocalSearchMCPServer
        print(LocalSearchMCPServer.SERVER_VERSION)
        return 0

    # Fast fail for malformed/invalid config files to avoid hanging MCP startup.
    try:
        cfg_path = WorkspaceManager.resolve_config_path(str(Path.cwd()))
        validate_config_file(cfg_path)
    except Exception as e:
        print(f"[sari] startup preflight failed: {e}", file=sys.stderr)
        print(
            "[sari] fix: use a valid JSON config file and keep db_path separate from config path "
            "(e.g. set SARI_DB_PATH=~/.local/share/sari/index.db).",
            file=sys.stderr,
        )
        return 2

    os.environ["SARI_FORMAT"] = ns.format

    if ns.http_api:
        _set_http_api_port(ns.http_api_port)
        if _should_http_daemon(ns):
            return _spawn_http_daemon(ns)
        return _run_http_server()

    if ns.transport == "http":
        _set_http_api_port(ns.http_api_port)
        if _should_http_daemon(ns):
            return _spawn_http_daemon(ns)
        return _run_http_server()

    if use_async:
        try:
            import asyncio
            from sari.mcp.async_server import AsyncLocalSearchMCPServer
            
            async def run_async():
                server = AsyncLocalSearchMCPServer(WorkspaceManager.resolve_workspace_root())
                server._original_stdout = clean_stdout
                await server.run()
                
            try:
                # Use clean_stdout for async server too
                asyncio.run(run_async())
            except KeyboardInterrupt:
                pass
            return 0
        except ImportError as e:
            print(f"Async MCP server unavailable: {e}. Falling back to sync.", file=sys.stderr)
        except Exception as e:
             print(f"Async MCP server failed: {e}. Falling back to sync.", file=sys.stderr)

    # stdio: always proxy to the daemon for stable multi-client operation.
    from sari.mcp.proxy import main as proxy_main
    proxy_main()
    return 0
