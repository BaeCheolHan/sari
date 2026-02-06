import json
import os
import pathlib
import fnmatch
from typing import List, Dict, Any, Set, Optional
from sari.core.settings import settings
from sari.core.workspace import WorkspaceManager
from .profiles import PROFILES, Profile

class ConfigManager:
    """
    Handles layered configuration merging with strict adherence to ARCHITECTURE.md.
    """
    
    def __init__(self, workspace_root: Optional[str] = None, manual_only: bool = False, settings_obj=None):
        self.workspace_root = pathlib.Path(workspace_root).resolve() if workspace_root else None
        self.manual_only = manual_only # If True, auto-detected profiles are just recommendations
        self.settings = settings_obj or settings
        self.active_profiles: List[str] = ["core"]
        self.recommended_profiles: List[str] = []
        
        # Aligned with ARCHITECTURE.md key schema
        self.include_add: Set[str] = set()
        self.exclude_add: Set[str] = set()
        self.include_remove: Set[str] = set()
        self.exclude_remove: Set[str] = set()

        # Internal flattened state for the engine
        self.final_extensions: Set[str] = set()
        self.final_filenames: Set[str] = set()
        self.final_exclude_dirs: Set[str] = {".git", "node_modules", ".venv", "dist", "build"}
        self.final_exclude_globs: Set[str] = set()

    def _load_sariignore(self) -> List[str]:
        """Load patterns from .sariignore if exists."""
        if not self.workspace_root: return []
        ignore_file = self.workspace_root / ".sariignore"
        if ignore_file.exists():
            return [line.strip() for line in ignore_file.read_text().splitlines() if line.strip() and not line.startswith("#")]
        return []

    def _load_gitignore(self) -> List[str]:
        if not self.workspace_root:
            return []
        try:
            from sari.core.utils.gitignore import load_gitignore
            return load_gitignore(self.workspace_root)
        except Exception:
            return []

    def is_project_root(self) -> bool:
        """Check for .sariroot boundary marker."""
        if not self.workspace_root: return False
        return (self.workspace_root / ".sariroot").exists()

    def detect_profiles(self) -> List[str]:
        """Scan with depth limit 2-3 and respect .sariignore."""
        if not self.workspace_root: return ["core"]
        
        ignore_patterns = self._load_sariignore()
        detected = ["core"]
        
        # Depth limited scan
        for depth in range(3):
            pattern = "*/" * depth if depth > 0 else ""
            for name, profile in PROFILES.items():
                if name == "core": continue
                for marker in profile.detect_files:
                    matches = list(self.workspace_root.glob(f"{pattern}{marker}"))
                    # Filter matches by .sariignore
                    valid_matches = []
                    for m in matches:
                        rel = str(m.relative_to(self.workspace_root))
                        ignored = False
                        for p in ignore_patterns:
                            # Match file directly or any parent directory
                            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, f"{p}*") or any(fnmatch.fnmatch(part, p) for part in rel.split(os.sep)):
                                ignored = True
                                break
                        if not ignored:
                            valid_matches.append(m)
                    
                    if valid_matches:
                        detected.append(name)
                        break
        
        self.recommended_profiles = list(dict.fromkeys(detected))
        if not self.manual_only:
            self.active_profiles = self.recommended_profiles
        return self.recommended_profiles

    def resolve_final_config(self) -> Dict[str, Any]:
        """
        Executes the 6-step merge logic from ARCHITECTURE.md:
        1. Core profile (always on)
        2. Auto-detected profiles
        3. Global config
        4. Workspace config
        5. include_add / exclude_add
        6. include_remove / exclude_remove
        """
        if self.workspace_root:
            try:
                WorkspaceManager.ensure_sari_dir(str(self.workspace_root))
            except Exception:
                pass

        ignore_patterns = self._load_sariignore()
        gitignore_lines = self._load_gitignore()
        self.detect_profiles()
        
        # 1 & 2. Profiles
        for p_name in self.active_profiles:
            p = PROFILES.get(p_name)
            if p:
                self.final_extensions.update(p.extensions)
                self.final_filenames.update(p.filenames)
                self.final_exclude_globs.update(p.globs)
        # Apply .sariignore to indexing
        self.final_exclude_globs.update(ignore_patterns)

        # 3 & 4. Load Config Files (Accumulate Overrides)
        global_path = pathlib.Path(self.settings.GLOBAL_CONFIG_DIR) / "config.json"
        ws_path = pathlib.Path(WorkspaceManager.resolve_config_path(str(self.workspace_root))) if self.workspace_root else None
        
        for path in [global_path, ws_path]:
            data = self._load_json(path)
            self.include_add.update(data.get("include_add", []))
            self.exclude_add.update(data.get("exclude_add", []))
            self.include_remove.update(data.get("include_remove", []))
            self.exclude_remove.update(data.get("exclude_remove", []))

        # 5. Apply include_add / exclude_add (Union)
        for item in self.include_add:
            if item.startswith("."):
                self.final_extensions.add(item)
            elif "*" in item or "?" in item:
                # Treat as include glob (fallback to filename include)
                self.final_filenames.add(item)
            else:
                self.final_filenames.add(item)
        self.final_exclude_globs.update(self.exclude_add)

        # 6. Apply include_remove / exclude_remove (Strict Exclusion)
        for item in self.include_remove:
            self.final_extensions.discard(item)
            self.final_filenames.discard(item)
        for item in self.exclude_remove:
            self.final_exclude_globs.discard(item)
            if item in self.final_exclude_dirs:
                self.final_exclude_dirs.remove(item)

        return self.to_dict(gitignore_lines)

    def _load_json(self, path: Optional[pathlib.Path]) -> Dict[str, Any]:
        if path and path.exists():
            try:
                with path.open("rb") as f:
                    head = f.read(16)
                if head.startswith(b"SQLite format 3"):
                    raise ValueError(
                        f"Invalid config file at {path}: detected SQLite DB; expected JSON."
                    )
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError(f"Invalid config shape at {path}: expected JSON object.")
                return data
            except Exception as e:
                raise ValueError(f"Failed to load config file {path}: {e}") from e
        return {}

    def to_dict(self, gitignore_lines: Optional[List[str]] = None) -> Dict[str, Any]:
        return {
            "root_id": WorkspaceManager.root_id(str(self.workspace_root)) if self.workspace_root else None,
            "active_profiles": self.active_profiles,
            "recommended_profiles": self.recommended_profiles,
            "final_extensions": sorted(list(self.final_extensions)),
            "final_filenames": sorted(list(self.final_filenames)),
            "final_exclude_dirs": sorted(list(self.final_exclude_dirs)),
            "final_exclude_globs": sorted(list(self.final_exclude_globs)),
            "gitignore_lines": gitignore_lines or [],
        }
