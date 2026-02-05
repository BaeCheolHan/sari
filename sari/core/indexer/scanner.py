import fnmatch
import os
from pathlib import Path
from typing import Iterable, List, Tuple, Optional
from sari.core.utils.gitignore import GitignoreMatcher

class Scanner:
    def __init__(self, cfg):
        self.cfg = cfg

    def iter_file_entries(self, root: Path, apply_exclude: bool = True) -> Iterable[Tuple[Path, os.stat_result, bool]]:
        exclude_dirs = set(getattr(self.cfg, "exclude_dirs", []))
        exclude_globs = list(getattr(self.cfg, "exclude_globs", []))
        gitignore_lines = list(getattr(self.cfg, "gitignore_lines", []))
        gitignore = GitignoreMatcher(gitignore_lines) if gitignore_lines else None
        include_ext = {e.lower() for e in getattr(self.cfg, "include_ext", [])}
        include_files = set(getattr(self.cfg, "include_files", []))
        include_all = not include_ext and not include_files

        for dirpath, dirnames, filenames in os.walk(root):
            if dirnames and apply_exclude:
                kept = []
                for d in dirnames:
                    if d in exclude_dirs: continue
                    rel_dir = str((Path(dirpath) / d).absolute().relative_to(root))
                    if any(fnmatch.fnmatch(rel_dir, pat) or fnmatch.fnmatch(d, pat) for pat in exclude_dirs):
                        continue
                    if gitignore and gitignore.is_ignored(rel_dir.replace(os.sep, "/"), is_dir=True):
                        continue
                    kept.append(d)
                dirnames[:] = kept
            
            for fn in filenames:
                p = Path(dirpath) / fn
                try:
                    rel = str(p.absolute().relative_to(root))
                except: continue
                
                excluded = any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(fn, pat) for pat in exclude_globs)
                if not excluded and gitignore:
                    rel_posix = rel.replace(os.sep, "/")
                    if gitignore.is_ignored(rel_posix, is_dir=False):
                        excluded = True
                if not excluded and exclude_dirs:
                    rel_parts = rel.split(os.sep)
                    for part in rel_parts:
                        if part in exclude_dirs or any(fnmatch.fnmatch(part, pat) for pat in exclude_dirs):
                            excluded = True
                            break
                
                try: st = p.stat()
                except: continue

                # Include filter (language-first policy)
                if not include_all:
                    rel_posix = rel.replace(os.sep, "/")
                    name = p.name
                    ext = p.suffix.lower()
                    included = False
                    if include_files:
                        for pattern in include_files:
                            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel_posix, pattern):
                                included = True
                                break
                    if not included and include_ext and ext in include_ext:
                        included = True
                    if not included:
                        continue

                if apply_exclude and excluded: continue
                yield p, st, excluded
