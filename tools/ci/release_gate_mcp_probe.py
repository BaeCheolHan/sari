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
from pathlib import Path


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


def _ensure_probe_repo_registered(repo: str) -> None:
    """call_flow 대상 repo가 워크스페이스에 등록되도록 보장한다."""
    from sari.core.config import AppConfig
    from sari.core.models import WorkspaceDTO
    from sari.db.migration import ensure_migrated
    from sari.db.repositories.workspace_repository import WorkspaceRepository
    from sari.db.schema import init_schema

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        return

    config = AppConfig.default()
    init_schema(config.db_path)
    ensure_migrated(config.db_path)
    workspace_repo = WorkspaceRepository(config.db_path)
    existing = workspace_repo.get_by_path(str(repo_path))
    if existing is not None and existing.is_active:
        return
    if existing is not None and not existing.is_active:
        workspace_repo.remove(str(repo_path))
    workspace_repo.add(
        WorkspaceDTO(
            path=str(repo_path),
            name=repo_path.name,
            indexed_at=None,
            is_active=True,
        )
    )


def _run_internal_client(
    timeout_initialize_sec: float = 30.0,
    timeout_tools_sec: float = 10.0,
    run_call_flow: bool = False,
    repo: str | None = None,
    use_local_server: bool = True,
) -> tuple[bool, dict[str, object]]:
    command = [sys.executable, "-m", "sari.cli.main", "mcp", "stdio"]
    if use_local_server:
        command.append("--local")
    proc = subprocess.Popen(
        command,
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
        if not run_call_flow:
            return (True, {"stage": "ok", "tool_count": len(tools_obj.get("tools", [])), "stderr_tail": stderr_lines[-20:]})

        if repo is None or repo.strip() == "":
            return (
                False,
                {
                    "stage": "call_flow",
                    "reason": "repo is required",
                    "stderr_tail": stderr_lines[-20:],
                },
            )

        search_payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {"repo": repo, "query": "McpServer", "limit": 3, "options": {"structured": 1}},
            },
        }
        proc.stdin.write(_frame(search_payload))
        proc.stdin.flush()
        search_resp = _read_by_id(proc.stdout, request_id=3, timeout_sec=max(timeout_tools_sec, 20.0))
        search_result = search_resp.get("result")
        if not isinstance(search_result, dict):
            return (
                False,
                {"stage": "call_flow/search", "reason": "search result payload invalid", "response": search_resp, "stderr_tail": stderr_lines[-20:]},
            )
        if bool(search_result.get("isError", False)):
            return (
                False,
                {"stage": "call_flow/search", "reason": "search returned isError", "response": search_resp, "stderr_tail": stderr_lines[-20:]},
            )
        structured = search_result.get("structuredContent")
        if not isinstance(structured, dict):
            return (
                False,
                {"stage": "call_flow/search", "reason": "search structuredContent missing", "response": search_resp, "stderr_tail": stderr_lines[-20:]},
            )
        items = structured.get("items")
        if not isinstance(items, list) or len(items) == 0:
            return (
                False,
                {"stage": "call_flow/search", "reason": "search items empty", "response": search_resp, "stderr_tail": stderr_lines[-20:]},
            )
        first_item = items[0]
        if not isinstance(first_item, dict):
            return (
                False,
                {"stage": "call_flow/search", "reason": "first item is not object", "response": search_resp, "stderr_tail": stderr_lines[-20:]},
            )
        rid = first_item.get("rid")
        relative_path = first_item.get("relative_path")
        if not isinstance(rid, str) or rid.strip() == "":
            rid = None
        if rid is None and (not isinstance(relative_path, str) or relative_path.strip() == ""):
            return (
                False,
                {
                    "stage": "call_flow/search",
                    "reason": "rid/relative_path missing from first item",
                    "response": search_resp,
                    "stderr_tail": stderr_lines[-20:],
                },
            )

        read_payload = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": (
                    {"repo": repo, "rid": rid, "mode": "symbol", "options": {"structured": 1}}
                    if rid is not None
                    else {"repo": repo, "mode": "file", "target": relative_path, "options": {"structured": 1}}
                ),
            },
        }
        proc.stdin.write(_frame(read_payload))
        proc.stdin.flush()
        read_resp = _read_by_id(proc.stdout, request_id=4, timeout_sec=max(timeout_tools_sec, 20.0))
        read_result = read_resp.get("result")
        if not isinstance(read_result, dict):
            return (
                False,
                {"stage": "call_flow/read", "reason": "read result payload invalid", "response": read_resp, "stderr_tail": stderr_lines[-20:]},
            )
        if bool(read_result.get("isError", False)):
            if rid is not None and isinstance(relative_path, str) and relative_path.strip() != "":
                read_payload_fallback = {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "read",
                        "arguments": {"repo": repo, "mode": "file", "target": relative_path, "options": {"structured": 1}},
                    },
                }
                proc.stdin.write(_frame(read_payload_fallback))
                proc.stdin.flush()
                read_resp_fallback = _read_by_id(proc.stdout, request_id=5, timeout_sec=max(timeout_tools_sec, 20.0))
                read_result_fallback = read_resp_fallback.get("result")
                if isinstance(read_result_fallback, dict) and not bool(read_result_fallback.get("isError", False)):
                    read_structured_fallback = read_result_fallback.get("structuredContent")
                    if isinstance(read_structured_fallback, dict):
                        return (
                            True,
                            {
                                "stage": "ok",
                                "tool_count": len(tools_obj.get("tools", [])),
                                "search_item_count": len(items),
                                "rid": rid,
                                "relative_path": relative_path,
                                "read_keys": sorted(read_structured_fallback.keys()),
                                "read_fallback_used": True,
                                "stderr_tail": stderr_lines[-20:],
                            },
                        )
            return (
                False,
                {"stage": "call_flow/read", "reason": "read returned isError", "response": read_resp, "stderr_tail": stderr_lines[-20:]},
            )
        read_structured = read_result.get("structuredContent")
        if not isinstance(read_structured, dict):
            return (
                False,
                {"stage": "call_flow/read", "reason": "read structuredContent missing", "response": read_resp, "stderr_tail": stderr_lines[-20:]},
            )

        return (
            True,
            {
                "stage": "ok",
                "tool_count": len(tools_obj.get("tools", [])),
                "search_item_count": len(items),
                "rid": rid,
                "relative_path": relative_path,
                "read_keys": sorted(read_structured.keys()),
                "stderr_tail": stderr_lines[-20:],
            },
        )
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


def _run_call_flow() -> int:
    """search -> read 도구 호출 흐름을 단일 세션에서 검증한다."""
    probe_repo = os.getenv("SARI_MCP_PROBE_REPO", "").strip()
    if probe_repo != "":
        _ensure_probe_repo_registered(probe_repo)
    index_result = subprocess.run(
        [sys.executable, "-m", "sari.cli.main", "index"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if index_result.returncode != 0:
        detail = {
            "stage": "index",
            "reason": "index command failed before call_flow",
            "exit_code": index_result.returncode,
            "stdout_tail": index_result.stdout.decode("utf-8", errors="replace").splitlines()[-20:],
            "stderr_tail": index_result.stderr.decode("utf-8", errors="replace").splitlines()[-20:],
        }
        _emit_summary(mode="call_flow", ok=False, detail=detail)
        raise RuntimeError(f"mcp call_flow probe failed: {detail}")
    ok, detail = _run_internal_client(run_call_flow=True, repo=probe_repo if probe_repo != "" else None)
    _emit_summary(mode="call_flow", ok=ok, detail=detail)
    if not ok:
        raise RuntimeError(f"mcp call_flow probe failed: {detail}")
    return 0


def _run_soak() -> int:
    """Gemini/Codex 동시 호출을 장시간 반복해 안정성을 점검한다."""
    probe_repo = os.getenv("SARI_MCP_PROBE_REPO", "").strip()
    if probe_repo == "":
        raise RuntimeError("SARI_MCP_PROBE_REPO is required for soak mode")
    _ensure_probe_repo_registered(probe_repo)
    duration_sec = int(os.getenv("SARI_MCP_SOAK_DURATION_SEC", "1800"))
    interval_sec = float(os.getenv("SARI_MCP_SOAK_INTERVAL_SEC", "1.0"))
    max_failure_rate = float(os.getenv("SARI_MCP_SOAK_MAX_FAILURE_RATE", "0.0"))
    max_timeout_failures = int(os.getenv("SARI_MCP_SOAK_MAX_TIMEOUT_FAILURES", "0"))
    min_attempts = int(os.getenv("SARI_MCP_SOAK_MIN_ATTEMPTS", "2"))
    requested_clients = int(os.getenv("SARI_MCP_SOAK_CLIENTS", "2"))
    client_count = max(2, requested_clients)
    lanes = tuple(f"client_{idx + 1}" for idx in range(client_count))
    # 본격 루프 전에 한 번 인덱싱해 콜드스타트 잡음을 줄인다.
    _ = subprocess.run([sys.executable, "-m", "sari.cli.main", "index"], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    started_at = time.time()
    attempts = 0
    successes = 0
    failures = 0
    timeout_failures = 0
    lane_failures: dict[str, int] = {lane: 0 for lane in lanes}
    fail_samples: list[dict[str, object]] = []

    while time.time() - started_at < float(duration_sec):
        lane_results: list[tuple[str, bool, dict[str, object]]] = []
        lock = threading.Lock()

        def _run_lane(lane: str) -> None:
            ok, detail = _run_internal_client(run_call_flow=True, repo=probe_repo, use_local_server=False)
            with lock:
                lane_results.append((lane, ok, detail))

        threads = [threading.Thread(target=_run_lane, args=(lane,), daemon=True) for lane in lanes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        for lane, ok, detail in lane_results:
            attempts += 1
            if ok:
                successes += 1
                continue
            failures += 1
            lane_failures[lane] = lane_failures.get(lane, 0) + 1
            detail_text = json.dumps(detail, ensure_ascii=False)
            if "timeout" in detail_text.lower():
                timeout_failures += 1
            if len(fail_samples) < 10:
                fail_samples.append({"lane": lane, "detail": detail})
        time.sleep(max(0.1, interval_sec))

    failure_rate = 0.0
    if attempts > 0:
        failure_rate = float(failures) / float(attempts)
    ok = attempts >= max(1, min_attempts) and failure_rate <= max_failure_rate and timeout_failures <= max_timeout_failures
    summary = {
        "duration_sec": duration_sec,
        "client_count": client_count,
        "attempts": attempts,
        "successes": successes,
        "failures": failures,
        "failure_rate": failure_rate,
        "timeout_failures": timeout_failures,
        "max_failure_rate": max_failure_rate,
        "max_timeout_failures": max_timeout_failures,
        "min_attempts": min_attempts,
        "lane_failures": lane_failures,
        "fail_samples": fail_samples,
    }
    _emit_summary(mode="soak", ok=ok, detail=summary)
    if not ok:
        raise RuntimeError(f"mcp soak failed: {summary}")
    return 0


def main() -> int:
    subprocess.run(["pkill", "-f", "sari.*daemon"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-f", "sari daemon run"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if len(sys.argv) < 2:
        raise SystemExit("usage: release_gate_mcp_probe.py [handshake|concurrency|call_flow|soak]")
    mode = sys.argv[1].strip().lower()
    if mode == "handshake":
        return _run_handshake()
    if mode == "concurrency":
        return _run_concurrency()
    if mode == "call_flow":
        return _run_call_flow()
    if mode == "soak":
        return _run_soak()
    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    raise SystemExit(main())
