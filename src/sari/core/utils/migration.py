import shutil
import pathlib
from sari.core.settings import settings

def cleanup_legacy_data():
    """
    Find and remove old .codex directories in workspace roots.
    """
    # This would typically be called during the first run of the new version
    pass

def migrate_global_config():
    """
    Move config from ~/.sari (old global) to ~/.config/sari (new global).
    Note: ~/.sari was used as global in some intermediate versions.
    """
    old_global = pathlib.Path.home() / ".sari"
    new_global = pathlib.Path(settings.GLOBAL_CONFIG_DIR)
    
    if old_global.exists() and old_global.is_dir() and old_global != new_global:
        # Move config.json if it exists
        old_cfg = old_global / "config.json"
        if old_cfg.exists():
            new_global.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_cfg, new_global / "config.json")
            # We keep the old dir for now but could mark it for deletion
