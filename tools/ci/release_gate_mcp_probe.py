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
        return json.loads(raw.decode("utf-8"))
    raise RuntimeError("timeout while reading MCP response")


def _read_by_id(fd, request_id: int, timeout_sec: float) -> dict[str, object]:
    end = time.time() + timeout_sec
    while time.time() < end:
        item = _read_one(fd, timeout_sec=max(0.1, end - time.time()))
        if item.get("id") == request_id:
            return item
    raise RuntimeError("timeout while reading MCP response by id")


def _drain_stderr(stderr, bucket: list[str]) -> None:
    """stderr 출력을 수집해 프로브 실패 원인에 포함한다."""
    if stderr is None:
        return
    for line in stderr:
        if isinstance(line, bytes):
            bucket.append(line.decode("utf-8", errors="replace").rstrip())
        else:
            bucket.append(str(line).rstrip())


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    """프로브 종료 시 자식 프로세스를 확실히 정리한다."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3.0)


def _run_internal_client(timeout_initialize_sec: float = 30.0, timeout_tools_sec: float = 10.0) -> tuple[bool, dict[str, object]]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "sari.cli.main", "mcp", "stdio", "--local"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    stderr_lines: list[str] = []
    stderr_reader = threading.Thread(target=_drain_stderr, args=(proc.stderr, stderr_lines), daemon=True)
    stderr_reader.start()
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
        resp1 = _read_one(proc.stdout, timeout_sec=timeout_initialize_sec)
        if "error" in resp1:
            return (False, {"stage": "initialize", "response": resp1, "stderr_tail": stderr_lines[-20:]})
        proc.stdin.write(_frame({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
        proc.stdin.flush()
        proc.stdin.write(_frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
        proc.stdin.flush()
        resp2 = _read_by_id(proc.stdout, request_id=2, timeout_sec=timeout_tools_sec)
        tools_obj = resp2.get("result")
        if not isinstance(tools_obj, dict) or not isinstance(tools_obj.get("tools"), list):
            return (
                False,
                {"stage": "tools/list", "response": resp2, "reason": "tools payload shape invalid", "stderr_tail": stderr_lines[-20:]},
            )
        return (True, {"stage": "ok", "tool_count": len(tools_obj.get("tools", [])), "stderr_tail": stderr_lines[-20:]})
    except Exception as exc:  # noqa: BLE001
        return (False, {"stage": "exception", "error": str(exc), "stderr_tail": stderr_lines[-20:]})
    finally:
        _terminate_process(proc)
        stderr_reader.join(timeout=1.0)


def _emit_summary(mode: str, ok: bool, detail: dict[str, object]) -> None:
    """release gate 로그 파싱을 위한 JSON 요약을 출력한다."""
    payload = {"mode": mode, "ok": ok, "detail": detail}
    print("PROBE_SUMMARY:" + json.dumps(payload, ensure_ascii=False))


def _run_handshake() -> int:
    ok, detail = _run_internal_client()
    _emit_summary(mode="handshake", ok=ok, detail=detail)
    if not ok:
        raise RuntimeError(f"mcp handshake probe failed: {detail}")
    return 0


def _run_concurrency() -> int:
    results: list[bool] = []
    details: list[dict[str, object]] = []
    lock = threading.Lock()

    def run_client() -> None:
        item, detail = _run_internal_client()
        with lock:
            results.append(item)
            details.append(detail)

    threads = [threading.Thread(target=run_client) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ok = bool(results) and all(results)
    _emit_summary(mode="concurrency", ok=ok, detail={"client_results": results, "client_details": details})
    if not ok:
        raise RuntimeError(f"concurrency discovery failed: {results}")
    return 0


def main() -> int:
    subprocess.run(["pkill", "-f", "sari.*daemon"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-f", "sari daemon run"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
