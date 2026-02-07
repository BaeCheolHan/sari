"""
Compatibility wrapper for legacy imports.

NOTE: This package is intentionally excluded from distribution to avoid
top-level name collisions. Use `sari.core` instead.
"""
from __future__ import annotations

import importlib
import sys

_SUBMODULES = [
    "config",
    "db",
    "dedup_queue",
    "engine_registry",
    "engine_runtime",
    "http_server",
    "indexer",
    "main",
    "models",
    "queue_pipeline",
    "ranking",
    "registry",
    "search_engine",
    "watcher",
    "workspace",
]

for _name in _SUBMODULES:
    _mod = importlib.import_module(f"sari.core.{_name}")
    sys.modules[f"app.{_name}"] = _mod
    globals()[_name] = _mod

__all__ = list(_SUBMODULES)
