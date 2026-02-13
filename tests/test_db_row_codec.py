from __future__ import annotations

import sqlite3
import zlib

import pytest

from sari.core.db.row_codec import (
    decode_file_content,
    normalize_root_row,
    normalize_search_row,
    row_content_value,
)


def test_normalize_root_row_maps_tuple_and_defaults():
    row = ("rid", "", "/real/ws", "repo", "", None, 2, 3, 4, 5, 6)
    normalized = normalize_root_row(row)
    assert normalized["root_id"] == "rid"
    assert normalized["path"] == "/real/ws"
    assert normalized["state"] == "ready"
    assert normalized["created_ts"] == 0
    assert normalized["symbol_count"] == 6


def test_row_content_value_supports_dict_and_tuple():
    assert row_content_value({"content": "hello"}) == "hello"
    assert row_content_value((b"raw",)) == b"raw"
    assert row_content_value(object()) is None


def test_decode_file_content_handles_utf8_and_latin1_and_zlib():
    assert decode_file_content("text", "rid/a.py") == "text"
    assert decode_file_content("text".encode("utf-8"), "rid/a.py") == "text"
    assert decode_file_content(b"\xff", "rid/a.py").encode("latin-1") == b"\xff"

    payload = b"print('ok')"
    compressed = b"ZLIB\0" + zlib.compress(payload)
    assert decode_file_content(compressed, "rid/a.py") == "print('ok')"


def test_decode_file_content_raises_for_corrupted_zlib_payload():
    with pytest.raises(RuntimeError):
        decode_file_content(b"ZLIB\0broken-stream", "rid/a.py")


def test_normalize_search_row_maps_tuple_to_file_columns():
    cols = ["path", "root_id", "repo"]
    row = ("rid/src/a.py", "rid")
    assert normalize_search_row(row, cols) == {
        "path": "rid/src/a.py",
        "root_id": "rid",
        "repo": None,
    }


def test_row_content_value_reads_sqlite_row():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE t(content TEXT)")
    cur.execute("INSERT INTO t(content) VALUES ('abc')")
    row = cur.execute("SELECT content FROM t").fetchone()
    assert row is not None
    assert row_content_value(row) == "abc"
    conn.close()
