import time


def test_search_v2_smoke(db):
    db.upsert_root("root", "/tmp/root", "/tmp/root")
    cur = db._write.cursor()
    # (path, rel_path, root_id, repo, mtime, size, content, content_hash, fts_content, last_seen_ts,
    #  deleted_ts, parse_status, parse_reason, ast_status, ast_reason, is_binary, is_minified, sampled, content_bytes, metadata_json)
    row = (
        "root/src/app.py",
        "src/app.py",
        "root",
        "repo",
        int(time.time()),
        10,
        "hello world",
        "h1",
        "hello world",
        int(time.time()),
        0,
        "ok",
        "",
        "ok",
        "",
        0,
        0,
        0,
        10,
        "{}",
    )
    db.upsert_files_tx(cur, [row])
    db._write.commit()
    hits, meta = db.search(type("opts", (), {"query": "hello", "limit": 10, "offset": 0, "repo": None, "root_ids": [], "total_mode": "exact"})())
    assert hits
    assert meta["total"] >= 1


def test_repo_candidates_smoke(db):
    db.upsert_root("root", "/tmp/root", "/tmp/root")
    cur = db._write.cursor()
    row = (
        "root/src/app.py",
        "src/app.py",
        "root",
        "repo",
        int(time.time()),
        10,
        "hello repo",
        "h1",
        "hello repo",
        int(time.time()),
        0,
        "ok",
        "",
        "ok",
        "",
        0,
        0,
        0,
        10,
        "{}",
    )
    db.upsert_files_tx(cur, [row])
    db._write.commit()
    cands = db.repo_candidates("app", limit=3, root_ids=["root"])
    assert cands


def test_context_snippet_smoke(db):
    now = int(time.time())
    db.upsert_root("root", "/tmp/root", "/tmp/root")
    cur = db._write.cursor()
    db.upsert_context_tx(cur, [("topic-a", "content alpha", "[]", "[]", "src", 0, 0, 0, now, now)])
    db.upsert_snippet_tx(
        cur,
        [
            (
                "tag1",
                "root/src/app.py",
                1,
                2,
                "print('hi')",
                "hash1",
                "",
                "",
                "repo",
                "root",
                "",
                "",
                now,
                now,
                "{}",
            )
        ],
    )
    db._write.commit()
    rows = db.search_contexts("alpha", limit=5)
    assert rows and rows[0].topic == "topic-a"
    snippets = db.list_snippets_by_tag("tag1")
    assert snippets and snippets[0].tag == "tag1"
