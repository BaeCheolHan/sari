import os
import sys
from pathlib import Path
from typing import Optional, Union

class PathUtils:
    @staticmethod
    def normalize(path: Union[str, Path]) -> str:
        """
        Conservative path normalization.
        - Unifies separators to '/'.
        - Strips trailing slashes.
        - Only absolute-fies if it's already an absolute-looking path or explicitly requested.
        - Preserves simple local paths like 'p1' for test compatibility.
        """
        p = str(path or "").strip()
        if not p: return os.getcwd().replace("\\", "/")
        
        # If it's a simple name without any path separators, keep it as is
        if "/" not in p and "\\" not in p and not p.startswith("."):
            return p
            
        try:
            # Handle user home and basic cleanup
            res = os.path.expanduser(p).replace("\\", "/")
            if len(res) > 1 and res.endswith("/"):
                res = res.rstrip("/")
            return res
        except Exception:
            return p.replace("\\", "/")

    @staticmethod
    def to_relative(path: str, root: str) -> str:
        n_path = PathUtils.normalize(path)
        n_root = PathUtils.normalize(root)
        
        if n_path == n_root: return "."
        if n_path.startswith(n_root + "/"):
            return n_path[len(n_root)+1:]
        return n_path

    @staticmethod
    def is_subpath(parent: str, child: str) -> bool:
        n_parent = PathUtils.normalize(parent)
        n_child = PathUtils.normalize(child)
        if n_parent == n_child: return True
        return n_child.startswith(n_parent + "/")

    @staticmethod
    def get_root_id(path: str) -> str:
        return PathUtils.normalize(path)
