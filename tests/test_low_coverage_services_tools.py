from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import unquote

from sari.core.services.index_service import IndexService
from sari.mcp.tools import diagnostics
from sari.mcp.tools._util import ErrorCode


class _FakeIndexer:
    def __init__(self, *, enabled=True, mode="on", depths=None):
        self.indexing_enabled = enabled
        self.indexer_mode = mode
        self._depths = list(depths or [])
        self.scan_once_called = 0
        self.request_rescan_called = 0
        self.enqueued = []
        self.status = SimpleNamespace(scanned_files=11, indexed_files=7)
        self.storage = SimpleNamespace(writer=SimpleNamespace(flush=MagicMock()))

    def scan_once(self):
        self.scan_once_called += 1

    def request_rescan(self):
        self.request_rescan_called += 1

    def get_queue_depths(self):
        if self._depths:
            return self._depths.pop(0)
        return {"fair_queue": 0, "priority_queue": 0, "db_writer": 0}

    def _enqueue_fsevent(self, evt):
        self.enqueued.append(evt)


def test_index_service_unavailable_or_disabled_modes():
    svc_none = IndexService(None)
    resp_none = svc_none.scan_once()
    assert resp_none["ok"] is False
    assert resp_none["code"] == ErrorCode.INTERNAL

    svc_off = IndexService(_FakeIndexer(enabled=False, mode="off"))
    resp_off = svc_off.scan_once()
    assert resp_off["ok"] is False
    assert resp_off["code"] == ErrorCode.ERR_INDEXER_DISABLED
    assert resp_off["data"]["mode"] == "off"

    svc_follower = IndexService(_FakeIndexer(enabled=False, mode="follower"))
    resp_follower = svc_follower.scan_once()
    assert resp_follower["ok"] is False
    assert resp_follower["code"] == ErrorCode.ERR_INDEXER_FOLLOWER
    assert resp_follower["data"]["mode"] == "follower"


def test_index_service_scan_once_waits_for_stable_queue(monkeypatch):
    idx = _FakeIndexer(
        depths=[
            {"fair_queue": 1, "priority_queue": 0, "db_writer": 0},
            {"fair_queue": 0, "priority_queue": 0, "db_writer": 0},
            {"fair_queue": 0, "priority_queue": 0, "db_writer": 0},
            {"fair_queue": 0, "priority_queue": 0, "db_writer": 0},
        ]
    )
    svc = IndexService(idx)
    monkeypatch.setattr("sari.core.services.index_service.time.sleep", lambda *_: None)

    resp = svc.scan_once()

    assert resp == {"ok": True, "scanned_files": 11, "indexed_files": 7}
    assert idx.scan_once_called == 1
    idx.storage.writer.flush.assert_called_once_with(timeout=2.0)


def test_index_service_scan_once_flush_and_status_error_paths(monkeypatch):
    idx = _FakeIndexer(depths=[{"fair_queue": 0, "priority_queue": 0, "db_writer": 0}] * 3)
    idx.storage.writer.flush.side_effect = RuntimeError("flush fail")

    class _BadStatus:
        @property
        def scanned_files(self):
            raise RuntimeError("bad status")

        @property
        def indexed_files(self):
            raise RuntimeError("bad status")

    idx.status = _BadStatus()
    svc = IndexService(idx)
    monkeypatch.setattr("sari.core.services.index_service.time.sleep", lambda *_: None)

    resp = svc.scan_once()

    assert resp == {"ok": True, "scanned_files": 0, "indexed_files": 0}


def test_index_service_rescan_paths():
    idx = _FakeIndexer()
    svc = IndexService(idx)
    assert svc.rescan() == {"ok": True}
    assert idx.request_rescan_called == 1

    class _ScanOnly:
        indexing_enabled = True
        indexer_mode = "on"

        def __init__(self):
            self.scan_once_called = 0

        def scan_once(self):
            self.scan_once_called += 1

    idx2 = _ScanOnly()
    svc2 = IndexService(idx2)
    assert svc2.rescan() == {"ok": True}
    assert idx2.scan_once_called == 1

    class _Unsupported:
        indexing_enabled = True
        indexer_mode = "on"

    svc3 = IndexService(_Unsupported())
    resp = svc3.rescan()
    assert resp["ok"] is False
    assert resp["code"] == ErrorCode.INTERNAL


def test_index_service_index_file_success_and_error():
    idx = _FakeIndexer()
    svc = IndexService(idx)
    resp = svc.index_file("/tmp/a.py")
    assert resp == {"ok": True}
    assert len(idx.enqueued) == 1
    assert idx.enqueued[0].path == "/tmp/a.py"

    idx_fail = _FakeIndexer()
    idx_fail._enqueue_fsevent = MagicMock(side_effect=RuntimeError("enqueue fail"))
    svc_fail = IndexService(idx_fail)
    resp_fail = svc_fail.index_file("/tmp/b.py")
    assert resp_fail["ok"] is False
    assert resp_fail["code"] == ErrorCode.INTERNAL
    assert "enqueue fail" in resp_fail["message"]


def test_diagnostics_handle_db_path_error_for_existing_and_missing_files(tmp_path):
    existing = tmp_path / "live.py"
    existing.write_text("print('x')", encoding="utf-8")
    missing = tmp_path / "missing.py"

    res_existing = diagnostics.handle_db_path_error("read_file", str(existing), [str(tmp_path)], db=None)
    text_existing = res_existing["content"][0]["text"]
    assert res_existing["isError"] is True
    assert "ERR_ROOT_OUT_OF_SCOPE" in text_existing
    assert str(tmp_path) in unquote(text_existing)

    res_missing = diagnostics.handle_db_path_error("read_file", str(missing), [str(tmp_path)], db=None)
    text_missing = res_missing["content"][0]["text"]
    assert res_missing["isError"] is True
    assert "NOT_INDEXED" in text_missing


def test_diagnostics_require_db_schema_paths():
    class _DBNoChecker:
        pass

    assert diagnostics.require_db_schema(_DBNoChecker(), "t", "table", ["c1"]) is None

    class _DBOk:
        @staticmethod
        def has_table_columns(table, columns):
            return True, []

    assert diagnostics.require_db_schema(_DBOk(), "tool", "table", ["c1"]) is None

    class _DBMissing:
        @staticmethod
        def has_table_columns(table, columns):
            return False, ["c2"]

    resp_missing = diagnostics.require_db_schema(_DBMissing(), "tool", "table", ["c1", "c2"])
    text_missing = resp_missing["content"][0]["text"]
    assert resp_missing["isError"] is True
    assert "DB_ERROR" in text_missing
    assert "table" in text_missing

    class _DBExplode:
        @staticmethod
        def has_table_columns(table, columns):
            raise RuntimeError("boom")

    assert diagnostics.require_db_schema(_DBExplode(), "tool", "table", ["c"]) is None
