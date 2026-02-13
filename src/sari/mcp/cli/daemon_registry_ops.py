from typing import Callable, Optional, Set


DaemonRow = dict[str, object]
DaemonRows = list[DaemonRow]


def get_registry_targets(
    host: str,
    port: int,
    pid_hint: Optional[int],
    *,
    registry_factory: Callable[[], object],
    default_host: str,
) -> tuple[Set[str], Set[int]]:
    boot_ids: Set[str] = set()
    http_pids: Set[int] = set()
    try:
        reg = registry_factory()
        data = reg.get_registry_snapshot(include_dead=True)
        daemons = data.get("daemons", {}) or {}
        workspaces = data.get("workspaces", {}) or {}
        for boot_id, info in daemons.items():
            if not isinstance(info, dict):
                continue
            if str(info.get("host") or default_host) != str(host):
                continue
            if int(info.get("port") or 0) != int(port):
                continue
            if pid_hint and int(info.get("pid") or 0) not in {0, int(pid_hint)}:
                continue
            boot_ids.add(str(boot_id))
        for ws_info in workspaces.values():
            if not isinstance(ws_info, dict):
                continue
            if str(ws_info.get("boot_id") or "") not in boot_ids:
                continue
            http_pid = int(ws_info.get("http_pid") or 0)
            if http_pid > 0:
                http_pids.add(http_pid)
    except Exception:
        pass
    return boot_ids, http_pids


def list_registry_daemons(
    *,
    registry_factory: Callable[[], object],
    kill_probe: Callable[[int], None],
    default_host: str,
) -> DaemonRows:
    out: DaemonRows = []
    try:
        reg = registry_factory()
        if hasattr(reg, "list_daemons"):
            for info in reg.list_daemons(include_dead=False):
                row = dict(info)
                out.append(row)
        else:
            data = reg._load()
            for boot_id, info in (data.get("daemons") or {}).items():
                if not isinstance(info, dict):
                    continue
                pid = int(info.get("pid") or 0)
                if pid <= 0:
                    continue
                try:
                    kill_probe(pid)
                except Exception:
                    continue
                row = dict(info)
                row["boot_id"] = boot_id
                row.setdefault("host", default_host)
                out.append(row)
    except Exception:
        return []
    out.sort(key=lambda x: float(x.get("last_seen_ts") or 0.0), reverse=True)
    return out


def list_registry_daemon_endpoints(
    *,
    rows_provider: Callable[[], DaemonRows],
    default_host: str,
) -> list[tuple[str, int]]:
    seen: Set[tuple[str, int]] = set()
    endpoints: list[tuple[str, int]] = []
    for row in rows_provider():
        host = str(row.get("host") or default_host)
        port = int(row.get("port") or 0)
        if port <= 0:
            continue
        key = (host, port)
        if key in seen:
            continue
        seen.add(key)
        endpoints.append(key)
    return endpoints


def discover_daemon_endpoints_from_processes(
    *,
    psutil_module,
    probe_daemon: Callable[[str, int], bool],
) -> list[tuple[str, int]]:
    if psutil_module is None:
        return []

    seen: Set[tuple[str, int]] = set()
    endpoints: list[tuple[str, int]] = []
    for proc in psutil_module.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.cmdline()).lower()
            if "sari" not in cmdline:
                continue
            if "daemon" not in cmdline:
                continue
            conns = proc.net_connections(kind="inet")
            for conn in conns:
                laddr = getattr(conn, "laddr", None)
                if not laddr:
                    continue
                host = getattr(laddr, "ip", None)
                port = getattr(laddr, "port", None)
                if host is None and isinstance(laddr, tuple) and len(laddr) >= 2:
                    host, port = laddr[0], laddr[1]
                if not isinstance(port, int) or port <= 0:
                    continue
                probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "::1", "", None) else str(host)
                key = (probe_host, port)
                if key in seen:
                    continue
                if probe_daemon(probe_host, port):
                    seen.add(key)
                    endpoints.append(key)
        except Exception:
            continue
    return endpoints
