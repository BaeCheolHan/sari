from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


def _language_id_for_path(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescriptreact",
        ".js": "javascript",
        ".jsx": "javascriptreact",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".c": "c",
        ".h": "c",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".hpp": "cpp",
    }.get(ext, ext.lstrip(".") or "plaintext")


def language_key_for_path(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".c": "cpp",
        ".h": "cpp",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".hpp": "cpp",
    }.get(ext, ext.lstrip("."))


def _candidate_lsp_commands(path: str) -> list[list[str]]:
    ext = Path(path).suffix.lower()
    if ext == ".py":
        return [["pyright-langserver", "--stdio"], ["basedpyright-langserver", "--stdio"], ["pylsp"]]
    if ext in {".ts", ".tsx", ".js", ".jsx"}:
        return [["typescript-language-server", "--stdio"]]
    if ext == ".go":
        return [["gopls"]]
    if ext == ".rs":
        return [["rust-analyzer"]]
    if ext in {".c", ".h", ".cc", ".cpp", ".hpp"}:
        return [["clangd", "--background-index=false"]]
    if ext in {".kt", ".kts"}:
        return [["kotlin-language-server"]]
    if ext == ".lua":
        return [["lua-language-server"]]
    return []


def _jsonrpc_encode(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _read_jsonrpc_message(fd: int, timeout_sec: float, buffer: bytearray) -> dict[str, object] | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        while True:
            header_end = buffer.find(b"\r\n\r\n")
            if header_end < 0:
                break
            header_blob = bytes(buffer[:header_end]).decode("ascii", errors="ignore")
            del buffer[: header_end + 4]
            content_length = 0
            for line in header_blob.splitlines():
                if line.lower().startswith("content-length:"):
                    try:
                        content_length = int(line.split(":", 1)[1].strip())
                    except Exception:
                        content_length = 0
            if content_length <= 0:
                continue
            while len(buffer) < content_length and time.time() < deadline:
                wait = max(0.0, deadline - time.time())
                if wait <= 0:
                    break
                ready, _, _ = select.select([fd], [], [], wait)
                if not ready:
                    continue
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                buffer.extend(chunk)
            if len(buffer) < content_length:
                return None
            body = bytes(buffer[:content_length])
            del buffer[:content_length]
            try:
                msg = json.loads(body.decode("utf-8", errors="ignore"))
            except Exception:
                continue
            if isinstance(msg, dict):
                return msg
        wait = max(0.0, deadline - time.time())
        if wait <= 0:
            break
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            return None
        buffer.extend(chunk)
    return None


def _flatten_document_symbols(symbols: list[object], path: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    kind_map = {5: "class", 6: "method", 12: "function", 13: "variable", 8: "field", 2: "module", 11: "interface"}

    def walk(item: object, parent_qual: str = "") -> None:
        if not isinstance(item, dict):
            return
        name = str(item.get("name") or "").strip()
        if not name:
            return
        kind_num = int(item.get("kind") or 0)
        kind = kind_map.get(kind_num, "symbol")
        rng = item.get("selectionRange") or item.get("range") or {}
        start = (rng.get("start") or {}) if isinstance(rng, dict) else {}
        end = (rng.get("end") or {}) if isinstance(rng, dict) else {}
        line = int(start.get("line") or 0) + 1
        end_line = int(end.get("line") or 0) + 1
        qual = f"{parent_qual}.{name}" if parent_qual else name
        sid = f"lsp:{hash((path, qual, line, end_line, kind)) & 0xFFFFFFFF:08x}"
        out.append(
            {
                "symbol_id": sid,
                "name": name,
                "kind": kind,
                "line": max(1, line),
                "end_line": max(line, end_line),
                "content": "",
                "parent": parent_qual.split(".")[-1] if parent_qual else "",
                "meta_json": "{}",
                "doc_comment": "",
                "qualname": qual,
                "importance_score": 0.0,
            }
        )
        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                walk(child, qual)

    for s in symbols:
        walk(s)
    return out


class _LspClient:
    def __init__(self, cmd: list[str]) -> None:
        self.cmd = list(cmd)
        self.proc: subprocess.Popen[bytes] | None = None
        self.buf = bytearray()
        self._lock = threading.Lock()

    def start(self, *, timeout_sec: float) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        self.stop()
        try:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if self.proc.stdout is None or self.proc.stdin is None:
                self.stop()
                return False
            os.set_blocking(self.proc.stdout.fileno(), False)
            return True
        except Exception:
            self.stop()
            return False

    def stop(self) -> None:
        p = self.proc
        self.proc = None
        if p is None:
            return
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=0.2)
        except Exception:
            pass

    def request_document_symbols(self, *, path: str, source: str, timeout_sec: float) -> list[dict[str, object]] | None:
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                if not self.start(timeout_sec=timeout_sec):
                    return None
            proc = self.proc
            if proc is None or proc.stdin is None or proc.stdout is None:
                return None
            uri = Path(path).resolve().as_uri()
            root_uri = Path(path).resolve().parent.as_uri()
            lang_id = _language_id_for_path(path)
            try:
                proc.stdin.write(
                    _jsonrpc_encode(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"processId": os.getpid(), "rootUri": root_uri, "capabilities": {}},
                        }
                    )
                )
                proc.stdin.flush()
                _read_jsonrpc_message(proc.stdout.fileno(), min(1.5, timeout_sec), self.buf)
                proc.stdin.write(_jsonrpc_encode({"jsonrpc": "2.0", "method": "initialized", "params": {}}))
                proc.stdin.write(
                    _jsonrpc_encode(
                        {
                            "jsonrpc": "2.0",
                            "method": "textDocument/didOpen",
                            "params": {
                                "textDocument": {
                                    "uri": uri,
                                    "languageId": lang_id,
                                    "version": 1,
                                    "text": source,
                                }
                            },
                        }
                    )
                )
                req_id = int(time.time() * 1000) % 1_000_000
                proc.stdin.write(
                    _jsonrpc_encode(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "method": "textDocument/documentSymbol",
                            "params": {"textDocument": {"uri": uri}},
                        }
                    )
                )
                proc.stdin.flush()
                symbols_resp: dict[str, object] | None = None
                for _ in range(10):
                    msg = _read_jsonrpc_message(proc.stdout.fileno(), timeout_sec, self.buf)
                    if not msg:
                        break
                    if msg.get("id") == req_id:
                        symbols_resp = msg
                        break
                if symbols_resp is None:
                    return None
                result = symbols_resp.get("result")
                if not isinstance(result, list):
                    return []
                return _flatten_document_symbols(result, path)
            except Exception:
                return None


class LSPHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, _LspClient] = {}
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._breaker: dict[str, dict[str, float | int]] = {}
        self._metrics: dict[str, float | int] = {
            "language_cold_start_count": 0,
            "lsp_restart_count": 0,
            "lsp_timeout_rate": 0.0,
            "lsp_timeout_count": 0,
            "lsp_request_count": 0,
            "lsp_backpressure_count": 0,
        }
        self._lang_metrics: dict[str, dict[str, int]] = {}
        self._timeout_sec = _env_float("SARI_LSP_TIMEOUT_SEC", 1.2)
        self._max_inflight = max(1, _env_int("SARI_LSP_MAX_INFLIGHT", 4))
        self._breaker_threshold = max(1, _env_int("SARI_LSP_BREAKER_THRESHOLD", 3))
        self._breaker_open_sec = max(1.0, _env_float("SARI_LSP_BREAKER_OPEN_SEC", 20.0))

    def get_or_start(self, language: str, source_path: str) -> _LspClient | None:
        now = time.time()
        with self._lock:
            st = self._breaker.setdefault(language, {"fail_count": 0, "open_until": 0.0})
            if float(st.get("open_until", 0.0) or 0.0) > now:
                return None
            cli = self._clients.get(language)
            if cli is not None and cli.proc is not None and cli.proc.poll() is None:
                return cli
            cmd = None
            for c in _candidate_lsp_commands(source_path):
                if shutil.which(c[0]):
                    cmd = c
                    break
            if not cmd:
                return None
            cli = _LspClient(cmd)
            if not cli.start(timeout_sec=self._timeout_sec):
                return None
            self._clients[language] = cli
            self._semaphores.setdefault(language, threading.Semaphore(self._max_inflight))
            self._lang_metrics.setdefault(
                language,
                {"requests": 0, "timeouts": 0, "restarts": 0, "backpressure": 0, "cold_starts": 0},
            )
            self._lang_metrics[language]["cold_starts"] = int(self._lang_metrics[language].get("cold_starts", 0)) + 1
            self._metrics["language_cold_start_count"] = int(self._metrics.get("language_cold_start_count", 0)) + 1
            return cli

    def _record_success(self, language: str) -> None:
        with self._lock:
            st = self._breaker.setdefault(language, {"fail_count": 0, "open_until": 0.0})
            st["fail_count"] = 0
            st["open_until"] = 0.0

    def _record_failure(self, language: str) -> None:
        with self._lock:
            st = self._breaker.setdefault(language, {"fail_count": 0, "open_until": 0.0})
            fc = int(st.get("fail_count", 0) or 0) + 1
            st["fail_count"] = fc
            if fc >= self._breaker_threshold:
                st["open_until"] = time.time() + self._breaker_open_sec

    def request_document_symbols(self, *, source_path: str, source: str) -> tuple[bool, list[dict[str, object]], str]:
        language = language_key_for_path(source_path)
        with self._lock:
            self._lang_metrics.setdefault(
                language,
                {"requests": 0, "timeouts": 0, "restarts": 0, "backpressure": 0, "cold_starts": 0},
            )
        sem = self._semaphores.setdefault(language, threading.Semaphore(self._max_inflight))
        if not sem.acquire(blocking=False):
            with self._lock:
                self._metrics["lsp_backpressure_count"] = int(self._metrics.get("lsp_backpressure_count", 0)) + 1
                self._lang_metrics[language]["backpressure"] = int(self._lang_metrics[language].get("backpressure", 0)) + 1
            return False, [], "ERR_BACKPRESSURE"
        try:
            with self._lock:
                self._metrics["lsp_request_count"] = int(self._metrics.get("lsp_request_count", 0)) + 1
                self._lang_metrics[language]["requests"] = int(self._lang_metrics[language].get("requests", 0)) + 1
            client = self.get_or_start(language, source_path)
            if client is None:
                return False, [], "ERR_LSP_UNAVAILABLE"

            out = client.request_document_symbols(path=source_path, source=source, timeout_sec=self._timeout_sec)
            if out is not None:
                self._record_success(language)
                return True, out, ""

            # one restart + one retry
            with self._lock:
                self._metrics["lsp_restart_count"] = int(self._metrics.get("lsp_restart_count", 0)) + 1
                self._lang_metrics[language]["restarts"] = int(self._lang_metrics[language].get("restarts", 0)) + 1
            client.stop()
            client = self.get_or_start(language, source_path)
            if client is None:
                self._record_failure(language)
                with self._lock:
                    self._metrics["lsp_timeout_count"] = int(self._metrics.get("lsp_timeout_count", 0)) + 1
                    self._lang_metrics[language]["timeouts"] = int(self._lang_metrics[language].get("timeouts", 0)) + 1
                return False, [], "ERR_LSP_UNAVAILABLE"
            out = client.request_document_symbols(path=source_path, source=source, timeout_sec=self._timeout_sec)
            if out is None:
                self._record_failure(language)
                with self._lock:
                    self._metrics["lsp_timeout_count"] = int(self._metrics.get("lsp_timeout_count", 0)) + 1
                    self._lang_metrics[language]["timeouts"] = int(self._lang_metrics[language].get("timeouts", 0)) + 1
                return False, [], "ERR_LSP_TIMEOUT"
            self._record_success(language)
            return True, out, ""
        finally:
            sem.release()

    def metrics_snapshot(self) -> dict[str, float | int]:
        with self._lock:
            req = int(self._metrics.get("lsp_request_count", 0) or 0)
            timeout_count = int(self._metrics.get("lsp_timeout_count", 0) or 0)
            timeout_rate = float(timeout_count) / float(req) if req > 0 else 0.0
            out = dict(self._metrics)
            out["lsp_timeout_rate"] = timeout_rate
            out["active_languages"] = len(self._clients)
            out["by_language"] = {k: dict(v) for k, v in self._lang_metrics.items()}
            return out


_HUB: LSPHub | None = None
_HUB_LOCK = threading.Lock()


def get_lsp_hub() -> LSPHub:
    global _HUB
    with _HUB_LOCK:
        if _HUB is None:
            _HUB = LSPHub()
        return _HUB


def prewarm_lsp_hub_from_db(db: Any, *, top_n: int = 3) -> int:
    """
    Best-effort prewarm for top languages in current workspace DB.
    Returns started language count.
    """
    conn = None
    if hasattr(db, "get_read_connection"):
        try:
            conn = db.get_read_connection()
        except Exception:
            conn = None
    if conn is None:
        conn = getattr(db, "_read", None)
    if conn is None:
        return 0
    try:
        rows = conn.execute("SELECT path FROM files WHERE deleted_ts = 0 LIMIT 2000").fetchall()
    except Exception:
        return 0
    lang_count: dict[str, int] = {}
    sample_path: dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict):
            path = str(row.get("path") or "")
        else:
            try:
                path = str(row["path"])
            except Exception:
                path = str(row[0] if isinstance(row, (list, tuple)) and row else "")
        if not path:
            continue
        # path is repo-relative, suffix is enough for language key.
        lang = language_key_for_path(path)
        if not lang:
            continue
        lang_count[lang] = int(lang_count.get(lang, 0)) + 1
        sample_path.setdefault(lang, path)
    if not lang_count:
        return 0
    top_langs = sorted(lang_count.items(), key=lambda kv: kv[1], reverse=True)[: max(1, int(top_n or 1))]
    hub = get_lsp_hub()
    started = 0
    for lang, _count in top_langs:
        # use a synthetic path with known extension to resolve command.
        p = sample_path.get(lang, f"/tmp/sari_prewarm_{lang}.py")
        cli = hub.get_or_start(lang, p)
        if cli is not None:
            started += 1
    return started
