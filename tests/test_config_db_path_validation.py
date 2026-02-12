import json

import pytest

from sari.core.config.main import Config


def test_config_load_rejects_db_path_equal_to_config_path_in_auto_discovery(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".sari"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text(json.dumps({"db_path": str(cfg_path)}), encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError):
        Config.load()
