import os
import sys
import hashlib
from pathlib import Path
from typing import Optional, List
from sari.core.settings import settings

class WorkspaceManager:
    """Manages workspace detection, boundary markers (.sariroot), and global paths."""
    settings = settings

    @staticmethod
    def set_settings(settings_obj):
        WorkspaceManager.settings = settings_obj

    @staticmethod
    def find_project_root(path: str) -> str:
        """Find the nearest directory containing .sariroot or .sari."""
        curr = Path(path).resolve()
        for parent in [curr] + list(curr.parents):
            if (parent / ".sariroot").exists() or (parent / ".sari").exists():
                return str(parent)
        return str(curr)

    @staticmethod
    def root_id(path: str) -> str:
        """Stable root id derived from project root boundary."""
        project_root = WorkspaceManager.find_project_root(path)
        digest = hashlib.sha1(project_root.encode("utf-8")).hexdigest()[:8]
        return f"root-{digest}"

    @staticmethod
    def _normalize_path(path: str, follow_symlinks: bool) -> str:
        expanded = os.path.expanduser(path)
        normalized = os.path.realpath(expanded) if follow_symlinks else os.path.abspath(expanded)
        if os.name == "nt": normalized = normalized.lower()
        return normalized.rstrip(os.sep)

    @staticmethod
    def resolve_workspace_roots(root_uri: Optional[str] = None, config_roots: Optional[List[str]] = None) -> List[str]:
        roots = []
        if config_roots:
            for r in config_roots:
                if r: roots.append(WorkspaceManager._normalize_path(r, WorkspaceManager.settings.FOLLOW_SYMLINKS))
        if root_uri:
            p = root_uri[7:] if root_uri.startswith("file://") else root_uri
            roots.append(WorkspaceManager._normalize_path(p, WorkspaceManager.settings.FOLLOW_SYMLINKS))
        if WorkspaceManager.settings.WORKSPACE_ROOT:
            roots.append(WorkspaceManager._normalize_path(WorkspaceManager.settings.WORKSPACE_ROOT, WorkspaceManager.settings.FOLLOW_SYMLINKS))
        return list(dict.fromkeys(roots)) or [os.getcwd()]

    @staticmethod
    def resolve_workspace_root(root_uri: Optional[str] = None) -> str:
        """Resolve a single workspace root (primary)."""
        roots = WorkspaceManager.resolve_workspace_roots(root_uri=root_uri)
        return roots[0] if roots else os.getcwd()

    @staticmethod
    def resolve_config_path(_repo_root: str) -> str:
        """Resolve global config path, honoring SARI_CONFIG if set."""
        val = WorkspaceManager.settings.CONFIG_PATH
        if val:
            return str(Path(os.path.expanduser(val)).resolve())
        return str(Path(WorkspaceManager.settings.GLOBAL_CONFIG_DIR) / "config.json")

    @staticmethod
    def get_global_data_dir() -> Path:
        return Path.home() / ".local" / "share" / "sari"

    @staticmethod
    def get_global_db_path() -> Path:
        return WorkspaceManager.get_global_data_dir() / "index.db"

    @staticmethod
    def get_global_log_dir() -> Path:
        return Path.home() / "Library" / "Logs" / "sari" if sys.platform == "darwin" else WorkspaceManager.get_global_data_dir() / "logs"

    @staticmethod
    def get_engine_index_dir(policy: Optional[str] = None, roots: Optional[List[str]] = None, root_id: Optional[str] = None) -> Path:
        """
        Index directory selector.
        Policy:
        - global (default): single index
        - roots_hash: per-roots group index (legacy-friendly)
        - per_root: one index per root_id
        """
        policy = (policy or WorkspaceManager.settings.ENGINE_INDEX_POLICY or "global").lower()
        base = WorkspaceManager.get_global_data_dir() / "index"
        if policy in {"global", "single"}:
            return base / "global"
        if policy in {"roots_hash", "legacy"}:
            roots = roots or []
            seed = "::".join(sorted([WorkspaceManager._normalize_path(r, WorkspaceManager.settings.FOLLOW_SYMLINKS) for r in roots]))
            digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8] if seed else "default"
            return base / f"roots-{digest}"
        if policy in {"per_root", "shard"}:
            rid = root_id or (WorkspaceManager.root_id(roots[0]) if roots else "root-default")
            return base / f"{rid}"
        return base / "global"

    @staticmethod
    def ensure_sari_dir(root_path: str) -> None:
        root = Path(root_path)
        sari_dir = root / WorkspaceManager.settings.WORKSPACE_CONFIG_DIR_NAME
        sari_dir.mkdir(parents=True, exist_ok=True)
        gitignore = root / ".gitignore"
        entry = f"{WorkspaceManager.settings.WORKSPACE_CONFIG_DIR_NAME}/"
        try:
            if not gitignore.exists():
                gitignore.write_text(entry + "\n", encoding="utf-8")
                return
            text = gitignore.read_text(encoding="utf-8")
            if entry not in text:
                with gitignore.open("a", encoding="utf-8") as f:
                    if not text.endswith("\n"):
                        f.write("\n")
                    f.write(entry + "\n")
        except Exception:
            pass
