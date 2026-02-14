from __future__ import annotations

from pathlib import Path
from typing import Any

from sari.core.lsp.hydrator import hydrate_file_symbols_from_text, sync_lsp_snapshot
from sari.mcp.tools._util import resolve_db_path, resolve_fs_path


def hydrate_file_symbols(
    *,
    db: Any,
    roots: list[str],
    repo: str,
    path: str,
) -> tuple[str | None, int]:
    db_path = resolve_db_path(path, roots, db=db)
    if not db_path:
        return None, 0

    fs_path = resolve_fs_path(path, roots)
    source = ""
    if fs_path and Path(fs_path).is_file():
        try:
            source = Path(fs_path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            source = ""
    if not source and hasattr(db, "read_file"):
        try:
            source = str(db.read_file(db_path) or "")
        except Exception:
            source = ""
    if not source:
        if hasattr(db, "mark_lsp_clean"):
            db.mark_lsp_clean(db_path, error="source_unavailable")
        return db_path, 0

    inserted_count, symbol_rows = hydrate_file_symbols_from_text(
        db=db,
        db_path=db_path,
        source_path=(fs_path or db_path),
        source=source,
    )
    try:
        sync_lsp_snapshot(
            db=db,
            db_path=db_path,
            source_path=(fs_path or db_path),
            symbol_rows=symbol_rows,
            lsp_version="ondemand-ts",
        )
    except Exception:
        if hasattr(db, "mark_lsp_dirty"):
            db.mark_lsp_dirty(db_path, reason="ondemand_sync_failed")

    return db_path, inserted_count


def hydrate_symbols_for_search(
    *,
    db: Any,
    roots: list[str],
    repo: str,
    query: str,
    max_files: int = 12,
) -> int:
    if not hasattr(db, "list_files"):
        return 0

    q = (query or "").strip().lower()
    total = 0
    files = db.list_files(limit=max(100, max_files * 4), repo=repo, root_ids=None)
    candidates: list[str] = []
    for item in files:
        path = str((item or {}).get("path") or "")
        if not path:
            continue
        if q and q in path.lower():
            candidates.append(path)
    if not candidates:
        candidates = [str((item or {}).get("path") or "") for item in files if (item or {}).get("path")]

    for p in candidates[:max_files]:
        _, inserted = hydrate_file_symbols(db=db, roots=roots, repo=repo, path=p)
        total += inserted
    return total
