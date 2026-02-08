import os
import json
import fnmatch
from typing import List, Optional, Dict, Any
from pathlib import Path

def resolve_config_path(root: str) -> str:
    """Find the config file in the given root."""
    for p in [".sari/config.json", "sari.json"]:
        full = os.path.join(root, p)
        if os.path.exists(full): return full
    return os.path.join(root, ".sari/config.json")

def validate_config_file(path: str) -> Optional[str]:
    """Validate JSON config file."""
    if not os.path.exists(path): return "File does not exist"
    try:
        with open(path, "r") as f: json.load(f)
        return None
    except Exception as e: return str(e)

class Config:
    def __init__(self, **kwargs):
        self.workspace_root = kwargs.get("workspace_root", os.getcwd())
        self.workspace_roots = kwargs.get("workspace_roots", [self.workspace_root])
        self.include_ext = kwargs.get("include_ext", [".py", ".js", ".ts", ".java", ".go", ".rs", ".rb", ".php", ".xml", ".yml", ".yaml", ".md", ".cs", ".swift", ".vue", ".hcl", ".tf", ".sql", ".txt"])
        self.exclude_dirs = kwargs.get("exclude_dirs", [".git", "node_modules", "target", "build", "dist", ".pytest_cache", "__pycache__", ".sari", ".venv", "venv", ".virtualenv", "env"])
        self.exclude_globs = kwargs.get("exclude_globs", [".venv*", "venv*", "env*", "*.egg-info"])
        self.max_depth = kwargs.get("max_depth", 20)
        # max_file_size는 Settings.MAX_PARSE_BYTES 사용 (통일)
        self.gitignore_lines = kwargs.get("gitignore_lines", [])
        self.http_api_host = kwargs.get("http_api_host", "127.0.0.1")
        self.http_api_port = kwargs.get("http_api_port", 47777)
        self.server_port = kwargs.get("server_port", 47777)
        self.db_path = kwargs.get("db_path", "")
        self.include_files = kwargs.get("include_files", [])
        
        # Indexer-required fields
        self.scan_interval_seconds = kwargs.get("scan_interval_seconds", 180)
        self.snippet_max_lines = kwargs.get("snippet_max_lines", 5)
        # max_file_bytes는 Settings.MAX_PARSE_BYTES 사용 (통일)
        self.redact_enabled = kwargs.get("redact_enabled", True)
        self.commit_batch_size = kwargs.get("commit_batch_size", 500)
        self.store_content = kwargs.get("store_content", True)
        
        # Post-init: synchronize workspace_root with workspace_roots
        if not self.workspace_roots:
            self.workspace_roots = [self.workspace_root]
        elif self.workspace_root not in self.workspace_roots:
            # workspace_roots가 제공되면 첫 번째 값을 workspace_root로 사용
            self.workspace_root = self.workspace_roots[0]

    @classmethod
    def load(cls, path: Optional[str] = None, workspace_root_override: Optional[str] = None):
        root = workspace_root_override or os.getcwd()
        cfg_path = path or resolve_config_path(root)
        
        data = {}
        if os.path.exists(cfg_path):
            # SQLite 파일 감지
            try:
                with open(cfg_path, "rb") as f:
                    head = f.read(16)
                if head.startswith(b"SQLite format 3"):
                    raise ValueError(
                        f"Invalid config file at {cfg_path}: detected SQLite DB. "
                        "Use a JSON config file."
                    )
            except ValueError:
                raise
            except Exception:
                pass
            
            # JSON 로드
            try:
                with open(cfg_path, "r") as f:
                    data = json.load(f)
                    data["workspace_root"] = root
            except:
                pass
        
        # db_path 검증
        db_path = data.get("db_path", "")
        if path and db_path:
            cfg_abs = os.path.abspath(os.path.expanduser(cfg_path))
            db_abs = os.path.abspath(os.path.expanduser(db_path))
            if cfg_abs == db_abs:
                raise ValueError(
                    f"Invalid configuration: db_path must not equal config path ({cfg_abs}). "
                    "Set db_path to a separate .db file."
                )
        
        defaults = cls.get_defaults(root)
        if data:
            merged = defaults.copy()
            merged.update(data)
            data = merged
        else:
            data = defaults

        # Enforce single global DB regardless of workspace-local config.
        try:
            from sari.core.workspace import WorkspaceManager
            ws_dir = Path(root).resolve() / WorkspaceManager.settings.WORKSPACE_CONFIG_DIR_NAME
            cfg_abs = Path(cfg_path).resolve()
            is_workspace_cfg = False
            try:
                cfg_abs.relative_to(ws_dir)
                is_workspace_cfg = True
            except Exception:
                is_workspace_cfg = False
            if is_workspace_cfg:
                data["db_path"] = str(WorkspaceManager.get_global_db_path())
        except Exception:
            pass

        if not data.get("db_path"):
            data["db_path"] = defaults.get("db_path", "")
        
        return cls(**data)

    @classmethod
    def get_defaults(cls, root: str) -> Dict[str, Any]:
        from sari.core.workspace import WorkspaceManager
        return {
            "workspace_root": root,
            "workspace_roots": [root],
            "include_ext": [".py", ".js", ".ts", ".java", ".go", ".rs", ".rb", ".php", ".xml", ".yml", ".yaml", ".md", ".cs", ".swift", ".vue", ".hcl", ".tf", ".sql", ".txt"],
            "exclude_dirs": [".git", "node_modules", "target", "build", "dist", ".pytest_cache", "__pycache__", ".sari", ".venv", "venv", ".virtualenv", "env"],
            "exclude_globs": [".venv*", "venv*", "env*", "*.egg-info"],
            "max_depth": 20,
            "db_path": str(WorkspaceManager.get_global_db_path()),
            "server_port": 47777,
            "http_api_host": "127.0.0.1",
            "http_api_port": 47777,
            "include_files": ["pom.xml", "package.json", "Dockerfile", "Makefile", "build.gradle", "settings.gradle"],
            "scan_interval_seconds": 180,
            "snippet_max_lines": 5,
            # max_file_bytes는 Settings.MAX_PARSE_BYTES 사용
            "redact_enabled": True,
            "commit_batch_size": 500,
            "store_content": True,
            "gitignore_lines": []
        }

    def save_paths_only(self, path: str, extra_paths: dict = None) -> None:
        """경로 관련 설정만 파일에 저장"""
        extra_paths = extra_paths or {}
        data = {}
        
        # 기존 설정 로드 (비경로 설정 보존)
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        
        if not isinstance(data, dict):
            data = {}
        
        # 경로 관련 설정 업데이트
        data["roots"] = self.workspace_roots
        data["db_path"] = self.db_path
        
        # 추가 경로 설정
        for k, v in extra_paths.items():
            if v:
                data[k] = v
        
        # 파일 저장
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def should_index(self, path: str) -> bool:
        from sari.core.settings import settings
        p = Path(path)
        if not p.exists(): return False
        if any(ex in p.parts for ex in self.exclude_dirs): return False
        if p.suffix.lower() not in self.include_ext:
            if p.name not in self.include_files: return False
        for pat in self.exclude_globs:
            if p.match(pat): return False
        try:
            # Settings.MAX_PARSE_BYTES 사용 (통일된 크기 제한)
            if p.is_file() and p.stat().st_size > settings.MAX_PARSE_BYTES: return False
        except: return False
        return True
