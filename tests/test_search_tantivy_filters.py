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
