"""
Compatibility wrapper for legacy imports.

NOTE: This package is intentionally excluded from distribution to avoid
top-level name collisions. Use `sari.mcp.tools` instead.
"""
from __future__ import annotations

import importlib
import sys

_SUBMODULES = [
    "_util",
    "deckard_guide",
    "doctor",
    "get_callers",
    "get_implementations",
    "index_file",
    "list_files",
    "read_file",
    "read_symbol",
    "registry",
    "repo_candidates",
    "rescan",
    "scan_once",
    "search",
    "search_api_endpoints",
    "search_symbols",
    "status",
]

for _name in _SUBMODULES:
    _mod = importlib.import_module(f"sari.mcp.tools.{_name}")
    sys.modules[f"mcp.tools.{_name}"] = _mod
    globals()[_name] = _mod

__all__ = list(_SUBMODULES)
