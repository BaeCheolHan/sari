"""
Compatibility wrapper for legacy imports.

NOTE: This package is intentionally excluded from distribution to avoid
top-level name collisions. Use `sari.mcp` instead.
"""
from __future__ import annotations

import importlib
import sys

_SUBMODULES = [
    "cli",
    "daemon",
    "proxy",
    "registry",
    "server",
    "session",
    "telemetry",
    "tools",
]

for _name in _SUBMODULES:
    _mod = importlib.import_module(f"sari.mcp.{_name}")
    sys.modules[f"mcp.{_name}"] = _mod
    globals()[_name] = _mod

__all__ = list(_SUBMODULES)
