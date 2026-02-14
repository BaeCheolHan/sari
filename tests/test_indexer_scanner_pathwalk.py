from pathlib import Path
from unittest.mock import MagicMock

from sari.core.config import Config
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import _scan_to_db


def test_scan_to_db_uses_scanner_instead_of_path_rglob(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("print('ok')\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "scan.db"))
    cfg = Config(**Config.get_defaults(str(ws)))

    def _forbid_rglob(_self, _pattern):
        raise AssertionError("Path.rglob must not be used in _scan_to_db")

    monkeypatch.setattr(Path, "rglob", _forbid_rglob, raising=True)

    status = _scan_to_db(cfg, db, logger=MagicMock())
    assert int(status.get("scanned_files", 0)) >= 1
    db.close_all()
