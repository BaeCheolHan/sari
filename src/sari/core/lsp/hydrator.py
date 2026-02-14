from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from sari.core.lsp.hub import get_lsp_hub

def _language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
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
    }.get(suffix, suffix.lstrip("."))


def _extract_symbols(source_path: str, source: str):
    ok, lsp_symbols = _extract_symbols_via_lsp(source_path, source)
    if ok:
        return lsp_symbols, "lsp"
    return _extract_symbols_via_regex(source_path, source), "regex"


def _extract_symbols_via_regex(source_path: str, source: str) -> list[dict[str, object]]:
    lines = source.splitlines()
    ext = Path(source_path).suffix.lower()
    out: list[dict[str, object]] = []
    kind = "symbol"
    if ext in {".py"}:
        class_re = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)")
        func_re = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
        for i, line in enumerate(lines, start=1):
            m = class_re.search(line)
            if m:
                name = m.group(1)
                out.append(
                    {
                        "symbol_id": f"rx:{hash((source_path, name, i, 'class')) & 0xFFFFFFFF:08x}",
                        "name": name,
                        "kind": "class",
                        "line": i,
                        "end_line": i,
                        "content": "",
                        "parent": "",
                        "meta_json": "{}",
                        "doc_comment": "",
                        "qualname": name,
                        "importance_score": 0.0,
                    }
                )
                continue
            m = func_re.search(line)
            if m:
                name = m.group(1)
                out.append(
                    {
                        "symbol_id": f"rx:{hash((source_path, name, i, 'function')) & 0xFFFFFFFF:08x}",
                        "name": name,
                        "kind": "function",
                        "line": i,
                        "end_line": i,
                        "content": "",
                        "parent": "",
                        "meta_json": "{}",
                        "doc_comment": "",
                        "qualname": name,
                        "importance_score": 0.0,
                    }
                )
        return out

    # Minimal generic fallback for non-python.
    generic_re = re.compile(r"^\s*(class|interface|struct|enum|function)\s+([A-Za-z_][A-Za-z0-9_]*)")
    for i, line in enumerate(lines, start=1):
        m = generic_re.search(line)
        if not m:
            continue
        kind, name = m.group(1), m.group(2)
        out.append(
            {
                "symbol_id": f"rx:{hash((source_path, name, i, kind)) & 0xFFFFFFFF:08x}",
                "name": name,
                "kind": kind,
                "line": i,
                "end_line": i,
                "content": "",
                "parent": "",
                "meta_json": "{}",
                "doc_comment": "",
                "qualname": name,
                "importance_score": 0.0,
            }
        )
    return out


def _extract_symbols_via_lsp(source_path: str, source: str) -> tuple[bool, list[dict[str, object]]]:
    hub = get_lsp_hub()
    ok, symbols, _err = hub.request_document_symbols(source_path=source_path, source=source)
    return ok, symbols


def hydrate_file_symbols_from_text(
    *,
    db: Any,
    db_path: str,
    source_path: str,
    source: str,
) -> tuple[int, list[dict[str, object]]]:
    symbols, _backend = _extract_symbols(source_path, source)
    root_id = db_path.split("/", 1)[0] if "/" in db_path else "root"
    rows: list[dict[str, object]] = []
    for sym in symbols:
        if isinstance(sym, dict):
            rows.append(
                {
                    "symbol_id": str(sym.get("symbol_id") or ""),
                    "path": db_path,
                    "root_id": root_id,
                    "name": str(sym.get("name") or ""),
                    "kind": str(sym.get("kind") or "unknown"),
                    "line": int(sym.get("line") or 0),
                    "end_line": int(sym.get("end_line") or 0),
                    "content": str(sym.get("content") or ""),
                    "parent": str(sym.get("parent") or ""),
                    "meta_json": str(sym.get("meta_json") or "{}"),
                    "doc_comment": str(sym.get("doc_comment") or ""),
                    "qualname": str(sym.get("qualname") or ""),
                    "importance_score": float(sym.get("importance_score") or 0.0),
                }
            )
        else:
            rows.append(
                {
                    "symbol_id": str(getattr(sym, "sid", "") or ""),
                    "path": db_path,
                    "root_id": root_id,
                    "name": str(getattr(sym, "name", "") or ""),
                    "kind": str(getattr(sym, "kind", "unknown") or "unknown"),
                    "line": int(getattr(sym, "line", 0) or 0),
                    "end_line": int(getattr(sym, "end_line", 0) or 0),
                    "content": str(getattr(sym, "content", "") or ""),
                    "parent": str(getattr(sym, "parent", "") or ""),
                    "meta_json": json.dumps(getattr(sym, "meta", {}) or {}, ensure_ascii=False),
                    "doc_comment": str(getattr(sym, "doc", "") or ""),
                    "qualname": str(getattr(sym, "qualname", "") or ""),
                    "importance_score": 0.0,
                }
            )

    if hasattr(db, "upsert_symbols_tx"):
        db.upsert_symbols_tx(None, rows, root_id=root_id)
    return len(rows), rows


def sync_lsp_snapshot(
    *,
    db: Any,
    db_path: str,
    source_path: str,
    symbol_rows: list[dict[str, object]],
    lsp_version: str = "hydrator-v1",
) -> None:
    conn = db.get_connection() if hasattr(db, "get_connection") else None
    if conn is None:
        return
    root_id = db_path.split("/", 1)[0] if "/" in db_path else "root"
    ts = int(time.time())
    row_version = ts
    language = _language_for_path(source_path)
    content_hash = ""
    try:
        meta = db.get_file_meta(db_path) if hasattr(db, "get_file_meta") else None
        if isinstance(meta, (list, tuple)) and len(meta) >= 3:
            content_hash = str(meta[2] or "")
    except Exception:
        content_hash = ""

    try:
        conn.execute("BEGIN IMMEDIATE TRANSACTION")
        conn.execute(
            """
            INSERT INTO lsp_indexed_files(path, root_id, language, content_hash, row_version, dirty, last_lsp_ts, lsp_version, updated_ts, created_ts)
            VALUES(?,?,?,?,?,0,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              root_id=excluded.root_id,
              language=excluded.language,
              content_hash=excluded.content_hash,
              row_version=excluded.row_version,
              dirty=0,
              last_lsp_ts=excluded.last_lsp_ts,
              lsp_version=excluded.lsp_version,
              error='',
              updated_ts=excluded.updated_ts
            """,
            (db_path, root_id, language, content_hash, row_version, ts, lsp_version, ts, ts),
        )
        conn.execute("DELETE FROM lsp_symbols WHERE path = ?", (db_path,))
        if symbol_rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO lsp_symbols(
                    symbol_id, path, root_id, name, kind, line, end_line, detail, qualname, row_version, lsp_ts
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        str(row.get("symbol_id") or ""),
                        db_path,
                        root_id,
                        str(row.get("name") or ""),
                        str(row.get("kind") or "unknown"),
                        int(row.get("line") or 0),
                        int(row.get("end_line") or 0),
                        str(row.get("content") or ""),
                        str(row.get("qualname") or ""),
                        row_version,
                        ts,
                    )
                    for row in symbol_rows
                ],
            )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
