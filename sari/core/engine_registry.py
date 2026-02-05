from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from sari.core.models import SearchHit, SearchOptions
from sari.core.engine_runtime import EmbeddedEngine, SqliteSearchEngineAdapter
from sari.core.settings import settings


class SearchEngineInterface(Protocol):
    def search_v2(self, opts: SearchOptions) -> Tuple[List[SearchHit], Dict[str, Any]]:
        ...

    def repo_candidates(self, q: str, limit: int = 3, root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        ...


class EngineRegistry:
    def __init__(self) -> None:
        self._factories: Dict[str, Callable[[Any, Any, Any], SearchEngineInterface]] = {}

    def register(self, name: str, factory: Callable[[Any, Any, Any], SearchEngineInterface]) -> None:
        self._factories[name] = factory

    def create(self, name: str, db: Any, cfg: Any = None, roots: Any = None) -> SearchEngineInterface:
        if name not in self._factories:
            raise KeyError(f"engine not registered: {name}")
        return self._factories[name](db, cfg, roots)

    def default(self, db: Any, cfg: Any = None, roots: Any = None) -> SearchEngineInterface:
        name = default_engine_name(cfg)
        return self.create(name, db, cfg, roots)


_REGISTRY = EngineRegistry()
_REGISTRY.register("sqlite", lambda db, _cfg, _roots: SqliteSearchEngineAdapter(db))
_REGISTRY.register("embedded", lambda db, cfg, roots: EmbeddedEngine(db, cfg, roots or []))


def get_registry() -> EngineRegistry:
    return _REGISTRY


def default_engine_name(cfg: Any = None) -> str:
    if cfg is not None:
        mode = (getattr(cfg, "engine_mode", "") or "").strip().lower()
        if mode in {"embedded", "sqlite"}:
            return mode
    return settings.ENGINE_MODE


def get_default_engine(db: Any, cfg: Any = None, roots: Any = None) -> SearchEngineInterface:
    return _REGISTRY.default(db, cfg, roots)
