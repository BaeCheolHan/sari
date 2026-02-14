import sqlite3

import pytest

from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import _cleanup_deleted_paths


def test_cleanup_deleted_paths_rolls_back_on_mid_failure(tmp_path):
    db = LocalSearchDB(str(tmp_path / "cleanup.db"))
    root_id = "rid-clean"
    db.upsert_root(root_id, str(tmp_path), str(tmp_path), label="root")
    conn = db._write
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO files(path, rel_path, root_id, repo, mtime, size, content, hash, fts_content, last_seen_ts, deleted_ts, status, error, parse_status, parse_error, ast_status, ast_reason, is_binary, is_minified, metadata_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"{root_id}/a.py",
            "a.py",
            root_id,
            "repo",
            10,
            1,
            b"x",
            "h",
            "x",
            1,  # old last_seen_ts
            0,
            "ok",
            "",
            "ok",
            "",
            "none",
            "none",
            0,
            0,
            "{}",
        ),
    )
    cur.execute(
        "INSERT INTO symbols(symbol_id, path, root_id, name, kind, line, end_line, content, parent, meta_json, doc_comment, qualname, importance_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sid-a", f"{root_id}/a.py", root_id, "A", "class", 1, 2, "class A: pass", "", "{}", "", "A", 1.0),
    )
    conn.commit()

    # Force failure after files update statement.
    conn.execute("DROP TABLE symbols")
    conn.commit()

    with pytest.raises(sqlite3.Error):
        _cleanup_deleted_paths(db, [root_id], now_ts=100, logger=None)

    row = db._read.execute("SELECT deleted_ts FROM files WHERE path = ?", (f"{root_id}/a.py",)).fetchone()
    assert int(row[0]) == 0
    db.close_all()
