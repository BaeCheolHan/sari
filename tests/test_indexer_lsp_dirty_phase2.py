from types import SimpleNamespace

from sari.core.indexer.main import Indexer


class _DB:
    def __init__(self):
        self.calls = []

    def mark_lsp_dirty(self, path, root_id="", reason=""):
        self.calls.append((path, root_id, reason))


def test_index_file_marks_lsp_dirty_before_rescan():
    idx = Indexer.__new__(Indexer)
    idx.db = _DB()
    idx.logger = None
    marker = {"called": 0}
    idx.request_rescan = lambda: marker.__setitem__("called", marker["called"] + 1)

    Indexer.index_file(idx, "/tmp/ws/repo/src/a.py")

    assert marker["called"] == 1
    assert idx.db.calls
    assert idx.db.calls[0][2] == "index_file"


def test_enqueue_fsevent_marks_src_and_dest_dirty():
    idx = Indexer.__new__(Indexer)
    idx.db = _DB()
    idx.logger = None
    idx.request_rescan = lambda: None

    evt = SimpleNamespace(path="/tmp/ws/repo/src/a.py", root="/tmp/ws/repo", dest_path="/tmp/ws/repo/src/b.py")
    Indexer._enqueue_fsevent(idx, evt)

    assert len(idx.db.calls) == 2
    assert idx.db.calls[0][2] == "watchdog_event"
    assert idx.db.calls[1][2] == "watchdog_event"
