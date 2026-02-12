from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import statistics
import time
from pathlib import Path

from sari.core.settings import settings
from sari.mcp.daemon import SariDaemon


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _client_once(host: str, port: int) -> None:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1.0)
        w.close()
        await w.wait_closed()
    except Exception:
        return


def _sample_row(daemon: SariDaemon, phase: str, elapsed_sec: float) -> dict[str, float | int | str]:
    rss_bytes = int(daemon._process_rss_bytes() or 0)
    return {
        "ts": time.time(),
        "elapsed_sec": round(float(elapsed_sec), 3),
        "phase": phase,
        "rss_bytes": rss_bytes,
        "rss_mb": round(rss_bytes / (1024 * 1024), 2),
        "event_queue_depth": int(getattr(daemon, "_event_queue_depth", 0) or 0),
        "leases": int(daemon.active_lease_count()),
        "connections": int(daemon._get_active_connections()),
    }


async def run_soak(
    *,
    load_sec: int,
    idle_sec: int,
    batch_size: int,
    batch_sleep_sec: float,
    sample_sec: float,
    output_json: Path | None,
) -> int:
    for name in ["mcp-daemon", "sari.registry", "sari.engine", "sari.watcher"]:
        logging.getLogger(name).setLevel(logging.ERROR)
    logging.getLogger().setLevel(logging.ERROR)

    settings.DAEMON_AUTOSTOP = False
    settings.DAEMON_HEARTBEAT_SEC = 5.0

    host = "127.0.0.1"
    port = _free_port()
    daemon = SariDaemon(host=host, port=port)
    daemon_task = asyncio.create_task(daemon.start_async())

    start_wait = time.monotonic()
    while daemon.server is None:
        if time.monotonic() - start_wait > 10:
            raise RuntimeError("daemon did not start in time")
        await asyncio.sleep(0.02)

    rows: list[dict[str, float | int | str]] = []
    run_start_mono = time.monotonic()
    next_sample_at = 0.0

    def take_sample(phase: str) -> None:
        row = _sample_row(daemon, phase, elapsed_sec=time.monotonic() - run_start_mono)
        rows.append(row)
        print(
            f"{phase} t+{row['elapsed_sec']:.1f}s rss_mb={row['rss_mb']} "
            f"q={row['event_queue_depth']} leases={row['leases']} conns={row['connections']}"
        )

    take_sample("start")

    load_deadline = time.monotonic() + max(0, int(load_sec))
    while time.monotonic() < load_deadline:
        await asyncio.gather(*[_client_once(host, port) for _ in range(max(1, batch_size))], return_exceptions=True)
        await asyncio.sleep(max(0.0, batch_sleep_sec))
        now = time.monotonic()
        if now >= next_sample_at:
            take_sample("load")
            next_sample_at = now + max(0.1, sample_sec)

    idle_deadline = time.monotonic() + max(0, int(idle_sec))
    while time.monotonic() < idle_deadline:
        take_sample("idle")
        await asyncio.sleep(max(0.1, sample_sec))

    take_sample("final")

    daemon.shutdown("memory_soak_done")
    try:
        await asyncio.wait_for(daemon_task, timeout=5)
    except asyncio.CancelledError:
        pass

    rss_values = [float(r["rss_mb"]) for r in rows]
    load_values = [float(r["rss_mb"]) for r in rows if str(r["phase"]) in {"start", "load"}]
    idle_values = [float(r["rss_mb"]) for r in rows if str(r["phase"]) in {"idle", "final"}]
    q_max = max(int(r["event_queue_depth"]) for r in rows) if rows else 0
    summary = {
        "samples": len(rows),
        "rss_start_mb": rss_values[0] if rss_values else 0.0,
        "rss_max_mb": max(rss_values) if rss_values else 0.0,
        "rss_end_mb": rss_values[-1] if rss_values else 0.0,
        "load_avg_mb": round(statistics.mean(load_values), 2) if load_values else 0.0,
        "idle_avg_mb": round(statistics.mean(idle_values), 2) if idle_values else 0.0,
        "q_max": q_max,
    }
    print("summary", json.dumps(summary, ensure_ascii=False))

    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"saved {output_json}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Run daemon memory soak and print RSS/queue trends.")
    p.add_argument("--load-sec", type=int, default=180)
    p.add_argument("--idle-sec", type=int, default=60)
    p.add_argument("--batch-size", type=int, default=120)
    p.add_argument("--batch-sleep-sec", type=float, default=0.03)
    p.add_argument("--sample-sec", type=float, default=5.0)
    p.add_argument("--output-json", type=Path, default=Path("artifacts/soak/daemon_memory_soak.json"))
    args = p.parse_args()
    return asyncio.run(
        run_soak(
            load_sec=args.load_sec,
            idle_sec=args.idle_sec,
            batch_size=args.batch_size,
            batch_sleep_sec=args.batch_sleep_sec,
            sample_sec=args.sample_sec,
            output_json=args.output_json,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
