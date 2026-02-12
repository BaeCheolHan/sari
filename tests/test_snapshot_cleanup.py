import json
import os
import tempfile
from unittest.mock import MagicMock

from sari.core.config import Config
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer


class _DoneProc:
    exitcode = 0

    def is_alive(self):
        return False


class _AliveProc:
    exitcode = None

    def __init__(self):
        self.terminated = False
        self.joined = False

    def is_alive(self):
        return True

    def terminate(self):
        self.terminated = True

    def join(self, timeout=None):
        self.joined = True


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


def test_stop_removes_active_worker_snapshot_artifacts(tmp_path):
    indexer = _make_indexer(tmp_path)
    proc = _AliveProc()

    snapshot_path = str(tmp_path / "index.db.snapshot.active")
    status_path = str(tmp_path / "index.db.snapshot.status.json")
    log_path = str(tmp_path / "index.db.snapshot.log")
    for p in [snapshot_path, status_path, log_path]:
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")

    indexer._worker_proc = proc
    indexer._worker_snapshot_path = snapshot_path
    indexer._worker_status_path = status_path
    indexer._worker_log_path = log_path

    indexer.stop()

    assert proc.terminated
    assert proc.joined
    assert not os.path.exists(snapshot_path)
    assert not os.path.exists(status_path)
    assert not os.path.exists(log_path)


def test_cleanup_stale_snapshot_artifacts_removes_old_files(tmp_path):
    indexer = _make_indexer(tmp_path)
    db_path = str(tmp_path / "index.db")

    old_snapshot = f"{db_path}.snapshot.1000"
    fresh_snapshot = f"{db_path}.snapshot.2000"
    for p in [old_snapshot, fresh_snapshot]:
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")

    now = 10_000
    old_ts = now - 600
    fresh_ts = now - 10
    os.utime(old_snapshot, (old_ts, old_ts))
    os.utime(fresh_snapshot, (fresh_ts, fresh_ts))

    indexer._cleanup_stale_snapshot_artifacts(now_ts=now, max_age_seconds=60)

    assert not os.path.exists(old_snapshot)
    assert os.path.exists(fresh_snapshot)


def test_snapshot_path_falls_back_when_db_path_is_not_string():
    cfg = MagicMock()
    cfg.scan_interval_seconds = 10
    db = MagicMock()
    indexer = Indexer(cfg, db)

    snapshot_path = indexer._snapshot_path()
    expected_base = os.path.join(tempfile.gettempdir(), "sari_snapshots", "index.db")
    assert snapshot_path.startswith(expected_base + ".snapshot.")


def test_finalize_worker_logs_when_status_file_missing(tmp_path):
    indexer = _make_indexer(tmp_path)
    indexer.logger = MagicMock()

    snapshot_path = str(tmp_path / "index.db.snapshot.worker")
    log_path = str(tmp_path / "index.db.snapshot.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("worker stderr line\n")

    indexer._worker_proc = _DoneProc()
    indexer._worker_snapshot_path = snapshot_path
    indexer._worker_status_path = str(tmp_path / "missing.status.json")
    indexer._worker_log_path = log_path

    indexer._finalize_worker_if_done()

    assert indexer.status.last_error == "worker status missing"
    assert indexer.logger.error.called
    logged_message = indexer.logger.error.call_args[0][0]
    assert "status file missing" in logged_message


def test_finalize_worker_logs_payload_traceback(tmp_path):
    indexer = _make_indexer(tmp_path)
    indexer.logger = MagicMock()

    snapshot_path = str(tmp_path / "index.db.snapshot.worker")
    status_path = str(tmp_path / "index.db.snapshot.status.json")
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "ok": False,
                "error": "spawn failed",
                "traceback": "Traceback (most recent call last): ...",
                "snapshot_path": snapshot_path,
            },
            f,
        )

    indexer._worker_proc = _DoneProc()
    indexer._worker_snapshot_path = snapshot_path
    indexer._worker_status_path = status_path
    indexer._worker_log_path = str(tmp_path / "index.db.snapshot.log")

    indexer._finalize_worker_if_done()

    assert indexer.status.last_error == "spawn failed"
    assert indexer.logger.error.called
    args = indexer.logger.error.call_args[0]
    assert "worker reported failure" in args[0]
