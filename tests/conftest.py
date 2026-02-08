import pytest
import os
import sys
from pathlib import Path

@pytest.fixture(autouse=True)
def sari_env(monkeypatch):
    monkeypatch.setenv("SARI_DAEMON_PORT", "48000")
    monkeypatch.setenv("SARI_DAEMON_IDLE_SEC", "3600")
    monkeypatch.setenv("SARI_TEST_MODE", "1")

@pytest.fixture

def db(tmp_path):

    from sari.core.db.main import LocalSearchDB

    db_file = tmp_path / f"sari_test_{os.getpid()}.db"

    

    # Enable Foreign Keys explicitly

    db_inst = LocalSearchDB(str(db_file))

    db_inst.db.close()

    db_inst.db = db_inst.db.__class__(str(db_file), pragmas={

        'journal_mode': 'wal', 'synchronous': 'normal', 

        'busy_timeout': 60000, 'foreign_keys': 1 

    })

    db_inst.db.connect()

    

    from sari.core.db.schema import init_schema

    with db_inst.db.atomic(): init_schema(db_inst.db.connection())

    

    return db_inst



@pytest.fixture(autouse=True)
def cleanup_mocks():
    yield
    from unittest.mock import patch
    patch.stopall()
