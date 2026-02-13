"""Row/content conversion helpers for LocalSearchDB."""

from __future__ import annotations

import sqlite3
import zlib
from typing import Dict, List, Optional


def normalize_root_row(row: object) -> Dict[str, object]:
    if isinstance(row, sqlite3.Row):
        data = dict(row)
    elif isinstance(row, dict):
        data = dict(row)
    else:
        vals = list(row) if isinstance(row, (list, tuple)) else []
        data = {
            "root_id": vals[0] if len(vals) > 0 else "",
            "root_path": vals[1] if len(vals) > 1 else "",
            "real_path": vals[2] if len(vals) > 2 else "",
            "label": vals[3] if len(vals) > 3 else "",
            "state": vals[4] if len(vals) > 4 else "",
            "created_ts": vals[5] if len(vals) > 5 else 0,
            "updated_ts": vals[6] if len(vals) > 6 else 0,
            "last_scan_ts": vals[7] if len(vals) > 7 else 0,
            "file_count": vals[8] if len(vals) > 8 else 0,
            "last_indexed_ts": vals[9] if len(vals) > 9 else 0,
            "symbol_count": vals[10] if len(vals) > 10 else 0,
        }
    root_path = str(data.get("root_path") or "")
    real_path = str(data.get("real_path") or "")
    path = root_path or real_path
    return {
        "root_id": str(data.get("root_id") or ""),
        "path": path,
        "root_path": root_path,
        "real_path": real_path,
        "label": str(data.get("label") or ""),
        "state": str(data.get("state") or "ready"),
        "file_count": int(data.get("file_count") or 0),
        "symbol_count": int(data.get("symbol_count") or 0),
        "created_ts": int(data.get("created_ts") or 0),
        "updated_ts": int(data.get("updated_ts") or 0),
        "last_scan_ts": int(data.get("last_scan_ts") or 0),
        "last_indexed_ts": int(data.get("last_indexed_ts") or 0),
    }


def row_content_value(row: object) -> object:
    if isinstance(row, sqlite3.Row):
        return row["content"]
    if isinstance(row, (list, tuple)):
        return row[0] if row else None
    if isinstance(row, dict):
        return row.get("content")
    return getattr(row, "content", None)


def decode_file_content(content: object, db_path: str) -> Optional[str]:
    if isinstance(content, bytes) and content.startswith(b"ZLIB\0"):
        try:
            content = zlib.decompress(content[5:])
        except Exception as de:
            raise RuntimeError(f"Corrupted compressed content for path: {db_path}") from de
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("latin-1")
    return str(content) if content is not None else None


def normalize_search_row(row: object, file_columns: List[str]) -> Dict[str, object]:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, (list, tuple)):
        return {k: row[idx] if idx < len(row) else None for idx, k in enumerate(file_columns)}
    return {}


def normalize_repo_stat_row(row: object) -> tuple[str, int]:
    if isinstance(row, sqlite3.Row):
        label = str(row["label"] or "")
        count = int(row["file_count"] or 0)
        return label, count
    if isinstance(row, (list, tuple)):
        label = str(row[0] or "")
        count = int(row[1] or 0)
        return label, count
    row_dict = dict(row) if isinstance(row, dict) else {}
    label = str(row_dict.get("label") or "")
    count = int(row_dict.get("file_count") or 0)
    return label, count
