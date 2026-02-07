import pytest
import os
import sys
from pathlib import Path

# --- CORE TRUTH: Force current environment into all tests ---
SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

# Ensure all subprocesses inherit the same library path
os.environ["PYTHONPATH"] = f"{SCRIPT_DIR}:{os.environ.get('PYTHONPATH', '')}"
os.environ["SARI_TEST_MODE"] = "1"

@pytest.fixture(autouse=True)
def sari_env(monkeypatch):
    """Clean and consistent environment for every test."""
    monkeypatch.setenv("SARI_DAEMON_PORT", "48000")
    monkeypatch.setenv("SARI_DAEMON_IDLE_SEC", "3600")

@pytest.fixture
def db(tmp_path):
    from sari.core.db.main import LocalSearchDB
    db_file = tmp_path / "sari_test.db"
    return LocalSearchDB(str(db_file))
