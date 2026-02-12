from sari.core.db.main import LocalSearchDB
from sari.core.models import SearchOptions
from sari.core.search_engine import SearchEngine


def test_tantivy_file_types_filter_applies(tmp_path):
    db = LocalSearchDB(str(tmp_path / "t.db"))
    engine = SearchEngine(db)
    opts = SearchOptions(query="x", file_types=["py"])

    hits = [
        {"path": "root/a.py", "repo": "r", "score": 1.0, "mtime": 0, "size": 1},
        {"path": "root/b.md", "repo": "r", "score": 1.0, "mtime": 0, "size": 1},
    ]

    results = engine._process_tantivy_hits(hits, opts)
    assert len(results) == 1
    assert results[0].path.endswith(".py")
    db.close_all()


def test_tantivy_zero_scores_are_handled_without_dropping_hits(tmp_path):
    class _DummyTantivy:
        def search(self, q, root_ids=None, limit=50):
            return [
                {
                    "path": "root/zero.py",
                    "repo": "r",
                    "score": 0.0,
                    "mtime": 0,
                    "size": 1,
                }
            ]

    db = LocalSearchDB(str(tmp_path / "t.db"))
    engine = SearchEngine(db, tantivy_engine=_DummyTantivy())
    opts = SearchOptions(query="x", limit=5)

    results, _ = engine.search(opts)

    assert len(results) == 1
    assert results[0].path == "root/zero.py"
    assert results[0].score == 0.0
    db.close_all()
