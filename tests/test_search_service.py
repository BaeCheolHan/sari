from types import SimpleNamespace

from sari.core.models import SearchHit
from sari.core.services.search_service import SearchService


def test_search_service_hybrid_tolerates_non_mapping_meta():
    keyword_hits = [SearchHit(path="a.py", hit_reason="keyword")]
    semantic_hits = [SearchHit(path="b.py", hit_reason="semantic")]

    class _Engine:
        @staticmethod
        def search(_opts):
            return keyword_hits, None

    class _Repo:
        @staticmethod
        def search_semantic(_query_vector, limit=10, root_ids=None):
            return semantic_hits

    class _DB:
        @staticmethod
        def search_repo():
            return _Repo()

    svc = SearchService(_DB(), _Engine(), indexer=None)
    opts = SimpleNamespace(limit=10, root_ids=[], query_vector=[0.1, 0.2])

    hits, meta = svc.search(opts)

    assert len(hits) == 2
    assert meta["engine"] == "hybrid-rrf"


def test_search_service_index_meta_fallback_when_to_meta_is_not_mapping():
    class _Status:
        @staticmethod
        def to_meta():
            return ["bad-meta"]

        index_ready = True
        indexed_files = 3
        scanned_files = 5
        errors = 0
        symbols_extracted = 7
        index_version = "v1"
        last_error = ""
        scan_started_ts = 10
        scan_finished_ts = 20

    svc = SearchService(db=None, engine=None, indexer=SimpleNamespace(status=_Status()))
    meta = svc.index_meta()

    assert isinstance(meta, dict)
    assert meta["index_ready"] is True
    assert meta["indexed_files"] == 3
