import time


def test_snippet_upsert_tool_row(db):
    db.upsert_root("root", "/tmp/root", "/tmp/root")
    now = int(time.time())
    row = (
        "tag1",
        "root/src/app.py",
        10,
        12,
        "print('hi')",
        "hash1",
        "before",
        "after",
        "repo1",
        "root",
        "note",
        "commit",
        now,
        now,
        "{}",
    )
    cur = db._write.cursor()
    db.upsert_snippet_tx(cur, [row])
    db._write.commit()
    rows = db.list_snippets_by_tag("tag1")
    assert rows
    assert rows[0].root_id == "root"
    assert rows[0].path == "root/src/app.py"


def test_context_search(db):
    now = int(time.time())
    cur = db._write.cursor()
    db.upsert_context_tx(cur, [("topic-a", "content alpha", "[]", "[]", "src", 0, 0, 0, now, now)])
    db._write.commit()
    rows = db.search_contexts("alpha", limit=5)
    assert rows
    assert rows[0].topic == "topic-a"
