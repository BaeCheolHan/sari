"""
Compatibility wrapper for legacy imports.

NOTE: This package is intentionally excluded from distribution to avoid
top-level name collisions. Use `sari.core` instead.
"""
from __future__ import annotations

import importlib
import sys

LEGACY_MODULE_MAP = {
    "config": "sari.core.config",
    "db": "sari.core.db",
    "dedup_queue": "sari.core.dedup_queue",
    "engine_registry": "sari.core.engine_registry",
    "engine_runtime": "sari.core.engine_runtime",
    "http_server": "sari.core.http_server",
    "indexer": "sari.core.indexer",
    "main": "sari.core.main",
    "models": "sari.core.models",
    "queue_pipeline": "sari.core.queue_pipeline",
    "ranking": "sari.core.ranking",
    "registry": "sari.core.server_registry",
    "search_engine": "sari.core.search_engine",
    "watcher": "sari.core.watcher",
    "workspace": "sari.core.workspace",
}


def resolve_legacy_module(name: str):
    target = LEGACY_MODULE_MAP.get(name)
    if not target:
        available = ", ".join(sorted(LEGACY_MODULE_MAP.keys()))
        raise ImportError(
            f"Legacy module 'app.{name}' is not supported. "
            f"Supported modules: {available}. Use 'sari.core' imports instead."
        )
    return importlib.import_module(target)


for _name in LEGACY_MODULE_MAP:
    _mod = resolve_legacy_module(_name)
    sys.modules[f"app.{_name}"] = _mod
    globals()[_name] = _mod


def __getattr__(name: str):
    return resolve_legacy_module(name)


__all__ = list(LEGACY_MODULE_MAP.keys()) + ["LEGACY_MODULE_MAP", "resolve_legacy_module"]
