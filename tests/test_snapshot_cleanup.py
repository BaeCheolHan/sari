import json
import os

from sari.core.config import Config
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer


class _DoneProc:
    exitcode = 0

    def is_alive(self):
        return False


def _make_indexer(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello')", encoding="utf-8")

    db = LocalSearchDB(str(tmp_path / "index.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    return Indexer(cfg, db)


def test_scan_once_removes_snapshot_file_after_success(tmp_path, monkeypatch):
    indexer = _make_indexer(tmp_path)
    captured = {}

    def fake_scan_to_db(_config, snapshot_db, _logger):
        captured["snapshot_path"] = snapshot_db.db_path
        return {
            "scan_started_ts": 1,
            "scan_finished_ts": 2,
            "scanned_files": 1,
            "indexed_files": 1,
            "symbols_extracted": 0,
            "errors": 0,
            "index_version": "2",
        }

    monkeypatch.setattr("sari.core.indexer.main._scan_to_db", fake_scan_to_db)

    indexer.scan_once()

    snapshot_path = captured["snapshot_path"]
    assert snapshot_path
    assert not os.path.exists(snapshot_path)


def test_finalize_worker_removes_snapshot_file_after_success(tmp_path):
    indexer = _make_indexer(tmp_path)

    snapshot_path = str(tmp_path / "index.db.snapshot.worker")
    snapshot_db = LocalSearchDB(snapshot_path)
    snapshot_db.close_all()

    status_path = str(tmp_path / "index.db.snapshot.status.json")
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "ok": True,
                "snapshot_path": snapshot_path,
                "status": {
                    "scan_started_ts": 1,
                    "scan_finished_ts": 2,
                    "scanned_files": 1,
                    "indexed_files": 1,
                    "symbols_extracted": 0,
                    "errors": 0,
                    "index_version": "2",
                },
            },
            f,
        )

    indexer._worker_proc = _DoneProc()
    indexer._worker_snapshot_path = snapshot_path
    indexer._worker_status_path = status_path

    indexer._finalize_worker_if_done()

    assert not os.path.exists(snapshot_path)
