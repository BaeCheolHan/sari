import json
import os
import time
import pytest
from pathlib import Path
from unittest.mock import patch
from sari.mcp.tools.doctor import execute_doctor, _check_db

class TestDoctorSelfHealing:
    
    @pytest.fixture
    def doctor_env(self, tmp_path):
        fake_home = tmp_path / "doctor_home"
        fake_home.mkdir()
        
        env = os.environ.copy()
        env["HOME"] = str(fake_home)
        env["SARI_REGISTRY_FILE"] = str(tmp_path / "doctor_registry.json")
        env["PYTHONPATH"] = os.getcwd() + ":" + env.get("PYTHONPATH", "")
        env["SARI_VERSION"] = "1.0.0"
        env["SARI_FORMAT"] = "json"
        # Use a high port to avoid real daemon interference
        env["SARI_DAEMON_PORT"] = "48999"
        
        return env

    def test_doctor_heals_stale_registry_entry(self, doctor_env):
        reg_file = Path(doctor_env["SARI_REGISTRY_FILE"])
        reg_file.parent.mkdir(parents=True, exist_ok=True)
        reg_file.write_text(json.dumps({
            "version": "2.0",
            "daemons": {
                "boot-x": {
                    "host": "127.0.0.1",
                    "port": 48999,
                    "pid": 999999,
                    "start_ts": time.time(),
                    "last_seen_ts": time.time(),
                    "draining": False,
                    "version": "1.0.0",
                }
            },
            "workspaces": {},
        }))

        with patch.dict("os.environ", doctor_env):
            res = execute_doctor({"auto_fix": False})
            daemon_res = next(r for r in res.get("results", []) if r["name"] == "Sari Daemon")
            assert not daemon_res["passed"]
            assert "stale registry entry" in daemon_res["error"]
            
            res = execute_doctor({"auto_fix": True})
            fix_res = next(r for r in res.get("auto_fix", []) if "Sari Daemon" in r["name"])
            assert fix_res["passed"]
            assert "pruned" in fix_res["error"]

    def test_doctor_heals_corrupted_registry(self, doctor_env):
        reg_file = Path(doctor_env["SARI_REGISTRY_FILE"])
        reg_file.parent.mkdir(parents=True, exist_ok=True)
        # Create a real corrupted state: valid JSON but wrong version or schema
        # Actually, let's mock ServerRegistry._load to fail
        with patch("sari.core.server_registry.ServerRegistry._load", side_effect=Exception("Corrupt")):
            with patch.dict("os.environ", doctor_env):
                res = execute_doctor({"auto_fix": True})
                fix_res = next(r for r in res.get("auto_fix", []) if "Registry" in r["name"])
                assert fix_res["passed"]

    def test_doctor_detects_version_mismatch(self, doctor_env):
        with patch("sari.mcp.tools.doctor._identify_sari_daemon") as mock_id:
            with patch("sari.mcp.tools.doctor.probe_sari_daemon", return_value=True):
                with patch("sari.mcp.cli.read_pid", return_value=123):
                    mock_id.return_value = {"name": "sari", "version": "0.0.1", "draining": False}
                
                with patch.dict("os.environ", doctor_env):
                    res = execute_doctor({"auto_fix": False})
                    daemon_res = next(r for r in res.get("results", []) if r["name"] == "Sari Daemon")
                    assert not daemon_res["passed"]
                    assert "Version mismatch" in daemon_res["error"]

    def test_doctor_includes_daemon_policy(self, doctor_env):
        with patch.dict("os.environ", doctor_env):
            res = execute_doctor({"auto_fix": False})
            policy_res = next(r for r in res.get("results", []) if r["name"] == "Daemon Policy")
            assert policy_res["passed"] is True
            assert "autostop_enabled=" in policy_res["error"]
            assert "heartbeat_sec=" in policy_res["error"]

    def test_doctor_policy_prefers_registry_http_endpoint(self, doctor_env):
        with patch("sari.mcp.tools.doctor.ServerRegistry.resolve_daemon_by_endpoint", return_value={"http_host": "127.0.0.1", "http_port": 53305}):
            with patch.dict("os.environ", doctor_env):
                res = execute_doctor({"auto_fix": False})
                policy_res = next(r for r in res.get("results", []) if r["name"] == "Daemon Policy")
                assert "http=127.0.0.1:53305" in policy_res["error"]

    def test_doctor_log_health_ignores_errors_counter_text(self, doctor_env, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "daemon.log").write_text(
            "\n".join(
                [
                    "2026-02-12 10:00:00,000 - sari.indexer.worker - INFO - indexer_worker_progress stage=enqueue scanned=200 errors=0",
                    "2026-02-12 10:00:01,000 - sari.indexer.worker - ERROR - actual error happened",
                ]
            ),
            encoding="utf-8",
        )
        env = dict(doctor_env)
        env["SARI_LOG_DIR"] = str(log_dir)
        with patch.dict("os.environ", env):
            res = execute_doctor({"auto_fix": False})
            log_res = next(r for r in res.get("results", []) if r["name"] == "Log Health")
            assert log_res["passed"] is False
            assert "Found 1 error(s)" in log_res["error"]

def share_path():
    return os.path.join("share", "sari")


def test_doctor_db_path_autofix_requires_auto_fix_flag(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"workspace_roots": [str(tmp_path)]}), encoding="utf-8")
    db_path = tmp_path / "missing.db"

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.db_path = str(db_path)

    with patch("sari.mcp.tools.doctor.WorkspaceManager.resolve_config_path", return_value=str(cfg_path)), \
         patch("sari.mcp.tools.doctor.Config.load", return_value=cfg):
        _check_db(str(tmp_path), allow_config_autofix=False)

    raw_after_no_fix = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "db_path" not in raw_after_no_fix

    with patch("sari.mcp.tools.doctor.WorkspaceManager.resolve_config_path", return_value=str(cfg_path)), \
         patch("sari.mcp.tools.doctor.Config.load", return_value=cfg):
        _check_db(str(tmp_path), allow_config_autofix=True)

    raw_after_fix = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert raw_after_fix["db_path"] == str(db_path)
