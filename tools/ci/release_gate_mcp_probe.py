#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import threading
import time


def _frame(payload: dict[str, object]) -> bytes:
    body = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _read_one(fd, timeout_sec: float) -> dict[str, object]:
    end = time.time() + timeout_sec
    buf = b""
    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        chunk = os.read(fd.fileno(), 4096)
        if not chunk:
            break
        buf += chunk
        if b"\r\n\r\n" not in buf:
            continue
        header, rest = buf.split(b"\r\n\r\n", 1)
        match = re.search(br"Content-Length:\s*(\d+)", header, re.IGNORECASE)
        if match is None:
            continue
        size = int(match.group(1))
        if len(rest) < size:
            continue
        raw = rest[:size]
        return json.loads(raw.decode("utf-8", errors="ignore"))
    raise RuntimeError("timeout while reading MCP response")


def _read_by_id(fd, request_id: int, timeout_sec: float) -> dict[str, object]:
    end = time.time() + timeout_sec
    while time.time() < end:
        item = _read_one(fd, timeout_sec=max(0.1, end - time.time()))
        if item.get("id") == request_id:
            return item
    raise RuntimeError("timeout while reading MCP response by id")


def _run_internal_client() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "sari.cli.main", "mcp", "stdio", "--local"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    try:
        proc.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "gate", "version": "1"},
                    },
                }
            )
        )
        proc.stdin.flush()
        resp1 = _read_one(proc.stdout, timeout_sec=30.0)
        if "error" in resp1:
            return 0
        proc.stdin.write(_frame({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
        proc.stdin.flush()
        proc.stdin.write(_frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
        proc.stdin.flush()
        resp2 = _read_by_id(proc.stdout, request_id=2, timeout_sec=10.0)
        tools_obj = resp2.get("result")
        if not isinstance(tools_obj, dict) or not isinstance(tools_obj.get("tools"), list):
            return 0
        return 1
    finally:
        proc.terminate()


def _run_handshake() -> int:
    ok = _run_internal_client()
    if ok != 1:
        raise RuntimeError("mcp handshake probe failed")
    return 0


def _run_concurrency() -> int:
    results: list[int] = []
    lock = threading.Lock()

    def run_client() -> None:
        item = _run_internal_client()
        with lock:
            results.append(item)

    threads = [threading.Thread(target=run_client) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if not results or any(item == 0 for item in results):
        raise RuntimeError(f"concurrency discovery failed: {results}")
    return 0


def main() -> int:
    subprocess.run('pkill -f "sari.*daemon"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run('pkill -f "sari daemon run"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if len(sys.argv) < 2:
        raise SystemExit("usage: release_gate_mcp_probe.py [handshake|concurrency]")
    mode = sys.argv[1].strip().lower()
    if mode == "handshake":
        return _run_handshake()
    if mode == "concurrency":
        return _run_concurrency()
    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    raise SystemExit(main())
