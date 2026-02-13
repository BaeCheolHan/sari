import os
import sys
import hashlib
import json
import shutil
from pathlib import Path
from typing import Optional, List
from sari.core.settings import settings
from sari.core.utils.path import PathUtils


class WorkspaceManager:
    """Manages workspace detection, explicit boundaries (.sariroot), and global paths."""
    settings = settings

    @staticmethod
    def set_settings(settings_obj):
        WorkspaceManager.settings = settings_obj

    @staticmethod
    def find_git_root(path: str) -> Optional[str]:
        """Find the nearest directory containing .git, stopping at home or root."""
        try:
            curr = Path(PathUtils.normalize(path))
            search_start = curr if curr.is_dir() else curr.parent
            home = Path.home()

            for parent in [search_start] + list(search_start.parents):
                if (parent / ".git").exists():
                    return str(parent)
                # Safety: Don't escape home directory or search root
                if parent == home or parent == parent.parent:
                    break
            return None
        except Exception:
            return None

    @staticmethod
    def find_project_root(path: str) -> str:
        """Find the nearest directory containing .sariroot."""
        try:
            curr = Path(PathUtils.normalize(path))
            for parent in [curr] + list(curr.parents):
                if (parent / ".sariroot").exists():
                    return str(parent)
            return str(curr)
        except Exception:
            return path

    @staticmethod
    def normalize_path(p: str) -> str:
        """Standard normalization for all workspace paths in Sari."""
        return PathUtils.normalize(p)

    @staticmethod
    def root_id(path: str) -> str:
        """Stable root id derived from project root boundary."""
        return PathUtils.normalize(WorkspaceManager.find_project_root(path))

    @staticmethod
    def root_id_for_workspace(workspace_root: str) -> str:
        """Stable root id for an explicit workspace root."""
        return PathUtils.normalize(workspace_root)

    @staticmethod
    def find_root_for_path(
            path: str, active_roots: Optional[List[str]] = None) -> Optional[str]:
        """Find which workspace root contains the given path."""
        p = PathUtils.normalize(path)
        roots = active_roots or WorkspaceManager.resolve_workspace_roots()

        # Sort by length descending to find most specific root first
        sorted_roots = sorted(roots, key=len, reverse=True)
        for r in sorted_roots:
            norm_r = PathUtils.normalize(r)
            if PathUtils.is_subpath(norm_r, p):
                return norm_r
        return None

    @staticmethod
    def resolve_workspace_roots(
            root_uri: Optional[str] = None, config_roots: Optional[List[str]] = None) -> List[str]:
        raw_roots = []
        if config_roots:
            for r in config_roots:
                if r:
                    raw_roots.append(PathUtils.normalize(r))

        # If explicit root_uri is provided, we use it directly without
        # expansion to respect caller's intent (e.g. tests)
        if root_uri:
            p = root_uri[7:] if root_uri.startswith("file://") else root_uri
            return [PathUtils.normalize(p)]

        if WorkspaceManager.settings.WORKSPACE_ROOT:
            raw_roots.append(
                PathUtils.normalize(
                    WorkspaceManager.settings.WORKSPACE_ROOT))

        if not raw_roots:
            raw_roots = [PathUtils.normalize(os.getcwd())]

        # Strict boundary policy: never auto-expand to parent git roots.
        return list(dict.fromkeys(raw_roots))

    @staticmethod
    def resolve_workspace_root(root_uri: Optional[str] = None) -> str:
        """
        Determine the primary workspace root.
        """
        roots = WorkspaceManager.resolve_workspace_roots(root_uri=root_uri)
        return roots[0] if roots else PathUtils.normalize(os.getcwd())

    @staticmethod
    def resolve_config_path(_repo_root: str) -> str:
        val = WorkspaceManager.settings.CONFIG_PATH
        if val:
            return PathUtils.normalize(val)
        repo_root = _repo_root or os.getcwd()
        ws_root = WorkspaceManager.find_project_root(repo_root)
        preferred = WorkspaceManager.workspace_config_path(ws_root)
        WorkspaceManager._migrate_legacy_workspace_config(ws_root, preferred)
        if preferred.exists():
            return str(preferred)
        return str(
            Path(
                WorkspaceManager.settings.GLOBAL_CONFIG_DIR) /
            "config.json")

    @staticmethod
    def workspace_config_path(root_path: str) -> Path:
        root = Path(PathUtils.normalize(root_path))
        return root / WorkspaceManager.settings.WORKSPACE_CONFIG_DIR_NAME / "mcp-config.json"

    @staticmethod
    def legacy_workspace_config_path(root_path: str) -> Path:
        root = Path(PathUtils.normalize(root_path))
        return root / WorkspaceManager.settings.WORKSPACE_CONFIG_DIR_NAME / "config.json"

    @staticmethod
    def _looks_like_sqlite(path: Path) -> bool:
        try:
            with path.open("rb") as f:
                head = f.read(16)
            return head.startswith(b"SQLite format 3")
        except Exception:
            return False

    @staticmethod
    def _migrate_legacy_workspace_config(
            root_path: str, preferred_path: Path) -> None:
        legacy = WorkspaceManager.legacy_workspace_config_path(root_path)
        if preferred_path.exists() or not legacy.exists():
            return
        if WorkspaceManager._looks_like_sqlite(legacy):
            return
        try:
            with legacy.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                preferred_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy, preferred_path)
        except Exception as e:
            import logging
            logging.getLogger("sari.workspace").debug(
                "Failed to migrate legacy config: %s", e)

    @staticmethod
    def get_global_data_dir() -> Path:
        return Path(PathUtils.normalize(
            str(Path.home() / ".local" / "share" / "sari")))

    @staticmethod
    def get_global_db_path() -> Path:
        return WorkspaceManager.get_global_data_dir() / "index.db"

    @staticmethod
    def get_workspace_data_dir(root_path: str) -> Path:
        root = Path(PathUtils.normalize(root_path))
        return root / WorkspaceManager.settings.WORKSPACE_CONFIG_DIR_NAME

    @staticmethod
    def get_workspace_db_path(root_path: str) -> Path:
        return WorkspaceManager.get_workspace_data_dir(root_path) / "index.db"

    @staticmethod
    def get_global_log_dir() -> Path:
        if WorkspaceManager.settings.LOG_DIR:
            return Path(PathUtils.normalize(WorkspaceManager.settings.LOG_DIR))
        base = Path.home() / "Library" / "Logs" / \
            "sari" if sys.platform == "darwin" else WorkspaceManager.get_global_data_dir().parent.parent / "log" / "sari"
        return Path(PathUtils.normalize(str(base)))

    @staticmethod
    def get_engine_index_dir(policy: Optional[str] = None,
                             roots: Optional[List[str]] = None,
                             root_id: Optional[str] = None) -> Path:
        policy = (
            policy or WorkspaceManager.settings.ENGINE_INDEX_POLICY or "global").lower()
        base = WorkspaceManager.get_global_data_dir() / "index"
        if policy in {"global", "single"}:
            return base / "global"
        if policy in {"roots_hash", "legacy"}:
            roots = roots or []
            seed = "::".join(sorted([PathUtils.normalize(r) for r in roots]))
            digest = hashlib.sha1(seed.encode(
                "utf-8")).hexdigest()[:8] if seed else "default"
            return base / f"roots-{digest}"
        if policy in {"per_root", "shard"}:
            rid = root_id or (
                WorkspaceManager.root_id_for_workspace(
                    roots[0]) if roots else "root-default")
            if os.sep in rid or "/" in rid or ":" in rid:
                sanitized = rid.replace(
                    os.sep,
                    "_").replace(
                    "/",
                    "_").replace(
                    ":",
                    "_").lstrip("_")
                if len(sanitized) > 100:
                    sanitized = sanitized[-100:]
                rid = f"ws-{sanitized}"
            return base / f"{rid}"
        return base / "global"

    @staticmethod
    def ensure_sari_dir(root_path: str) -> None:
        root = Path(PathUtils.normalize(root_path))
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
        except Exception as e:
            import logging
            logging.getLogger("sari.workspace").debug(
                "Failed to update gitignore: %s", e)

    @staticmethod
    def ensure_global_config() -> Path:
        """Ensure global config directory and a default config.json exist."""
        global_dir = Path(WorkspaceManager.settings.GLOBAL_CONFIG_DIR)
        config_path = global_dir / "config.json"

        if not config_path.exists():
            global_dir.mkdir(parents=True, exist_ok=True)
            default_config = {
                "workspace_roots": [],
                "include_ext": [
                    ".py",
                    ".js",
                    ".ts",
                    ".java",
                    ".kt",
                    ".md",
                    ".json",
                    ".sql",
                    ".gradle",
                    ".kts"],
                "exclude_dirs": [
                    ".git",
                    "node_modules",
                    ".worktrees",
                    ".venv",
                    "build",
                    "target",
                    ".gradle",
                    ".idea"],
                "db_path": str(
                    WorkspaceManager.get_global_db_path())}
            try:
                config_path.write_text(
                    json.dumps(
                        default_config,
                        indent=2),
                    encoding="utf-8")
            except Exception as e:
                import logging
                logging.getLogger("sari.workspace").error(
                    f"Failed to create default global config: {e}")

        return config_path
