"""Snapshot DB merge helpers for LocalSearchDB."""

from __future__ import annotations

import os
from typing import Iterable, Optional


def merge_snapshot_into_main(
    conn,
    snapshot_path: str,
    file_columns: Iterable[str],
    logger: Optional[object] = None,
) -> bool:
    if not snapshot_path or not os.path.exists(snapshot_path):
        return False

    attached = False
    try:
        conn.execute("ATTACH DATABASE ? AS snapshot", (snapshot_path,))
        attached = True
        conn.execute("BEGIN IMMEDIATE TRANSACTION")
        try:
            for tbl in [
                "roots",
                "files",
                "symbols",
                "symbol_relations",
                "snippets",
                "failed_tasks",
                "embeddings",
            ]:
                if tbl == "files":
                    cols = ", ".join(file_columns)
                    conn.execute(
                        f"INSERT OR REPLACE INTO main.files({cols}) SELECT {cols} FROM snapshot.files"
                    )
                else:
                    conn.execute(
                        f"INSERT OR REPLACE INTO main.{tbl} SELECT * FROM snapshot.{tbl}"
                    )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
    finally:
        if attached:
            try:
                conn.execute("DETACH DATABASE snapshot")
            except Exception as detach_error:
                if logger is not None and hasattr(logger, "debug"):
                    logger.debug("Failed to detach snapshot: %s", detach_error)

    return True
