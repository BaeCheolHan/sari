import os
from pathlib import Path
from typing import Optional
from sari.version import __version__
from sari.core.constants import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT, DEFAULT_HTTP_PORT

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    HAS_PYDANTIC = True
except ImportError:
    class BaseSettings:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    def SettingsConfigDict(*args, **kwargs): return {}
    HAS_PYDANTIC = False


class Settings(BaseSettings):
    if HAS_PYDANTIC:
        model_config = SettingsConfigDict(
            env_prefix="SARI_",
            case_sensitive=True,
            extra="ignore"
        )

    # --- CORE RELEVANT SETTINGS ---
    VERSION: str = __version__
    WORKSPACE_ROOT: str = str(Path.cwd())
    LOG_DIR: str = str(Path.home() / ".local" / "share" / "sari" / "logs")
    LOG_RETENTION_DAYS: int = 14
    CONFIG_PATH: Optional[str] = None
    GLOBAL_CONFIG_DIR: str = str(Path.home() / ".config" / "sari")
    WORKSPACE_CONFIG_DIR_NAME: str = ".sari"
    ENGINE_INDEX_POLICY: str = "global"
    ENGINE_RELOAD_MS: int = 1000
    ENGINE_INDEX_MEM_MB: int = 128
    FOLLOW_SYMLINKS: bool = False
    ENGINE_MODE: Optional[str] = "embedded"
    ENGINE_AUTO_INSTALL: bool = True
    MANUAL_ONLY: bool = False
    PERSIST_PATHS: bool = False

    # --- STORAGE & CONTENT ---
    STORE_CONTENT: bool = True
    STORE_CONTENT_COMPRESS: bool = True
    STORE_CONTENT_COMPRESS_LEVEL: int = 6
    AST_CACHE_ENTRIES: int = 1000

    # --- DAEMON SETTINGS ---
    DAEMON_HOST: str = DEFAULT_DAEMON_HOST
    DAEMON_PORT: int = DEFAULT_DAEMON_PORT
    HTTP_API_PORT: int = DEFAULT_HTTP_PORT
    # Default policy: autostop on last session close.
    # Idle timeout is opt-in by setting SARI_DAEMON_IDLE_SEC > 0.
    DAEMON_IDLE_SEC: int = 0
    DAEMON_TIMEOUT_SEC: int = 5
    DAEMON_AUTOSTART: bool = False
    DAEMON_HEARTBEAT_SEC: int = 5
    DAEMON_IDLE_WITH_ACTIVE: bool = False
    DAEMON_DRAIN_GRACE_SEC: int = 0
    DAEMON_AUTOSTOP: bool = True
    DAEMON_AUTOSTOP_GRACE_SEC: int = 60

    # --- MCP SETTINGS ---
    SEARCH_FIRST_MODE: bool = True
    MCP_QUEUE_SIZE: int = 1000

    # --- FEATURE TOGGLES ---
    ENABLE_FTS: bool = True
    ENABLE_AST: bool = True
    DEBUG: bool = False
    FTS_REBUILD_ON_START: bool = False

    # --- INTERNAL PERFORMANCE DEFAULTS ---
    MMAP_SIZE: int = 30 * 1024 * 1024 * 1024  # 30GB
    PAGE_SIZE: int = 65536
    MAX_DEPTH: int = 20

    # --- WORKER LIMITS ---
    MAX_PARSE_BYTES: int = 10 * 1024 * 1024  # 10MB
    MAX_AST_BYTES: int = 1 * 1024 * 1024     # 1MB
    ENGINE_MAX_DOC_BYTES: int = 500 * 1024   # 500KB
    FTS_MAX_BYTES: int = 1000000             # 1MB
    REDACT_ENABLED: bool = True

    @property
    def db_path(self) -> str:
        return str(Path.home() / ".local" / "share" / "sari" / "index.db")

    def get_int(self, key: str, default: int) -> int:
        val = getattr(self, key, None)
        if val is not None:
            return int(val)
        return int(os.environ.get(f"SARI_{key}", default))

    def get_bool(self, key: str, default: bool) -> bool:
        val = getattr(self, key, None)
        if val is not None:
            return bool(val)
        env = os.environ.get(f"SARI_{key}", "").lower()
        if not env:
            return default
        return env in ("true", "1", "yes", "on")


settings = Settings()
