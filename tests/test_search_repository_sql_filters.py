from sari.core.models import SearchOptions


def _insert_file_row(cur, row):
    cur.execute(
        """
        INSERT INTO files (
            path, rel_path, root_id, repo, mtime, size, content, hash, fts_content,
            last_seen_ts, deleted_ts, status, error, parse_status, parse_error,
            ast_status, ast_reason, is_binary, is_minified, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        row,
    )


def test_sqlite_search_file_type_filter_applied_in_repository(db):
    rid = "root-1"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    cur = db._write.cursor()
    _insert_file_row(
        cur,
        (
            f"{rid}/src/main.py",
            "src/main.py",
            rid,
            "repo",
            10,
            20,
            "print('code')",
            "h1",
            "print code",
            0,
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
    _insert_file_row(
        cur,
        (
            f"{rid}/src/theme.css",
            "src/theme.css",
            rid,
            "repo",
            10,
            20,
            "body { color: red; }",
            "h2",
            "body color red",
            0,
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
    db._write.commit()

    opts = SearchOptions(query="code", file_types=["py"], limit=20, root_ids=[rid])
    hits, _meta = db._search_sqlite(opts)

    assert len(hits) == 1
    assert hits[0].path.endswith("main.py")


def test_sqlite_search_path_and_exclude_filters_applied_in_repository(db):
    rid = "root-1"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    cur = db._write.cursor()
    rows = [
        (
            f"{rid}/src/logic.py",
            "src/logic.py",
            rid,
            "repo",
            11,
            20,
            "def logic(): pass",
            "h1",
            "logic code",
            0,
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
        (
            f"{rid}/src/legacy/old.py",
            "src/legacy/old.py",
            rid,
            "repo",
            11,
            20,
            "def old(): pass",
            "h2",
            "legacy logic code",
            0,
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
        (
            f"{rid}/tests/test_logic.py",
            "tests/test_logic.py",
            rid,
            "repo",
            11,
            20,
            "def test_logic(): pass",
            "h3",
            "test logic code",
            0,
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
    ]
    for row in rows:
        _insert_file_row(cur, row)
    db._write.commit()

    opts = SearchOptions(
        query="logic",
        path_pattern="src/**",
        exclude_patterns=["src/legacy/**"],
        limit=20,
        root_ids=[rid],
    )
    hits, _meta = db._search_sqlite(opts)
    paths = [hit.path for hit in hits]

    assert f"{rid}/src/logic.py" in paths
    assert f"{rid}/src/legacy/old.py" not in paths
    assert all(path.startswith(f"{rid}/src/") for path in paths)


def test_process_search_results_tolerates_non_numeric_size(db, monkeypatch):
    repo = db.search_repo
    rows = [("rid/a.py", "repo", 1, "1024KB", "token", "a.py", "", 0.0)]
    hits = repo._process_search_results(rows, "token")
    assert len(hits) == 1
    assert hits[0].size == 0
