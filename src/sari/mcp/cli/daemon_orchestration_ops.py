import argparse
import sys
from typing import Callable, Optional


def handle_existing_daemon(
    params: dict[str, object],
    *,
    kill_orphan_daemons: Callable[[], int],
    identify_daemon: Callable[[str, int], Optional[dict]],
    needs_upgrade_or_drain: Callable[[Optional[dict]], bool],
    read_pid: Callable[[str, int], Optional[int]],
    stop_daemon: Callable[[argparse.Namespace], int],
) -> Optional[int]:
    kill_orphan_daemons()

    host = str(params["host"])
    port = int(params["port"])
    workspace_root = str(params["workspace_root"])
    registry = params["registry"]
    explicit_port = bool(params["explicit_port"])
    force_start = bool(params["force_start"])

    identify = identify_daemon(host, port)
    if not identify:
        return None

    if explicit_port:
        ws_inst = registry.resolve_workspace_daemon(str(workspace_root))
        same_instance = bool(ws_inst and int(ws_inst.get("port", 0)) == int(port))
        if not same_instance:
            stop_args = argparse.Namespace(daemon_host=host, daemon_port=port)
            stop_daemon(stop_args)
            identify = identify_daemon(host, port)
            if identify:
                print(f"❌ Port {port} is occupied by another running daemon.", file=sys.stderr)
                return 1

    if not force_start and not needs_upgrade_or_drain(identify):
        pid = read_pid(host, port)
        print(f"✅ Daemon already running on {host}:{port}")
        if pid:
            print(f"   PID: {pid}")
        return 0

    stop_args = argparse.Namespace(daemon_host=host, daemon_port=port)
    stop_daemon(stop_args)
    identify = identify_daemon(host, port)
    if identify:
        print(f"❌ Failed to replace existing daemon on {host}:{port}.", file=sys.stderr)
        return 1
    return None
