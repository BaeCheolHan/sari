import logging
from dataclasses import dataclass
from typing import Mapping, Optional, TypeAlias
from sari.core.workspace import WorkspaceManager
from sari.core.engine.tantivy_engine import TantivyEngine
from sari.core.search_engine import SearchEngine
from sari.core.settings import settings

logger = logging.getLogger("sari.engine")

Doc: TypeAlias = dict[str, object]
DocInput: TypeAlias = Mapping[str, object]
SearchRow: TypeAlias = dict[str, object]
SearchRows: TypeAlias = list[SearchRow]
SearchMeta: TypeAlias = dict[str, object]
SearchV2Result: TypeAlias = tuple[list[object], SearchMeta]


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


@dataclass
class EngineMeta:
    engine_mode: str
    engine_ready: bool
    engine_version: str = "unknown"
    index_version: str = ""
    reason: str = ""
    hint: str = ""
    tokenizer_ready: bool = True
    tokenizer_bundle_tag: str = ""
    tokenizer_bundle_path: str = ""


class EngineRuntime:
    """Manages the Tantivy engine (Phase 5)."""

    def __init__(self, roots: list[str], settings_obj: object = None):
        self.roots = roots
        self.settings = settings_obj or settings
        self.root_ids = [WorkspaceManager.root_id(r) for r in roots]
        self.policy = self.settings.ENGINE_INDEX_POLICY
        # Phase 5: Unified global path with policy fallback
        self.index_dir = WorkspaceManager.get_engine_index_dir(
            roots=roots, root_id=self.root_ids[0] if self.root_ids else None)
        self.engine: Optional[TantivyEngine] = None
        self.engines: dict[str, TantivyEngine] = {}

    def initialize(self):
        try:
            if self.policy in {"per_root", "shard"} and self.roots:
                for root in self.roots:
                    rid = WorkspaceManager.root_id(root)
                    index_dir = WorkspaceManager.get_engine_index_dir(
                        policy=self.policy, roots=self.roots, root_id=rid)
                    self.engines[rid] = TantivyEngine(
                        str(index_dir), logger=logger, settings_obj=self.settings)
                self.engine = EngineRouter(self.engines)
                logger.info("Per-root engine(s) ready.")
            else:
                self.engine = TantivyEngine(
                    str(self.index_dir), logger=logger, settings_obj=self.settings)
                logger.info(f"Global engine ready at {self.index_dir}")
        except Exception as e:
            logger.error(f"Engine init failed: {e}")

    def upsert_documents(self, docs: list[Doc], commit: bool = True):
        if self.engine:
            self.engine.upsert_documents(docs, commit=commit)

    def delete_documents(self, doc_ids: list[str], commit: bool = True):
        if self.engine:
            self.engine.delete_documents(doc_ids, commit=commit)

    def search(self,
               query: str,
               root_id: Optional[str] = None,
               limit: int = 50) -> SearchRows:
        return self.engine.search(
            query,
            root_id=root_id,
            limit=limit) if self.engine else []

    def status(self) -> EngineMeta:
        ready = bool(self.engine)
        if not ready:
            return EngineMeta(
                engine_mode="embedded",
                engine_ready=False,
                reason="NOT_INSTALLED",
                hint="sari --cmd engine install")
        version = getattr(
            getattr(
                self.engine,
                "_tantivy",
                None),
            "__version__",
            "unknown")
        return EngineMeta(
            engine_mode="embedded",
            engine_ready=True,
            engine_version=version)

    def close(self) -> None:
        if self.engine and hasattr(self.engine, "close"):
            try:
                self.engine.close()
            except Exception:
                pass


class EngineRouter:
    """Routes requests to per-root engines when policy=per_root."""

    def __init__(self, engines: dict[str, TantivyEngine]):
        self.engines = engines

    def _extract_root_id(self, doc_id: str) -> Optional[str]:
        if not doc_id:
            return None
        return doc_id.split("/", 1)[0] if "/" in doc_id else None

    def upsert_documents(self, docs: list[DocInput], commit: bool = True) -> None:
        buckets: dict[str, list[Doc]] = {}
        for d in docs or []:
            if not isinstance(d, Mapping):
                continue
            rid = d.get("root_id") or self._extract_root_id(
                d.get("doc_id", ""))
            if not rid:
                continue
            buckets.setdefault(rid, []).append(dict(d))
        for rid, batch in buckets.items():
            engine = self.engines.get(rid)
            if engine:
                engine.upsert_documents(batch, commit=commit)

    def delete_documents(self, doc_ids: list[str], commit: bool = True) -> None:
        buckets: dict[str, list[str]] = {}
        for doc_id in doc_ids or []:
            rid = self._extract_root_id(doc_id)
            if not rid:
                continue
            buckets.setdefault(rid, []).append(doc_id)
        for rid, batch in buckets.items():
            engine = self.engines.get(rid)
            if engine:
                engine.delete_documents(batch, commit=commit)

    def commit(self) -> None:
        for engine in self.engines.values():
            if hasattr(engine, "commit"):
                engine.commit()

    def search(self,
               query: str,
               root_id: Optional[str] = None,
               limit: int = 50) -> SearchRows:
        if root_id:
            engine = self.engines.get(root_id)
            return engine.search(
                query,
                root_id=root_id,
                limit=limit) if engine else []
        results: SearchRows = []
        for rid, engine in self.engines.items():
            results.extend(engine.search(query, root_id=rid, limit=limit))
        results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        return results[:limit]


class EmbeddedEngine:
    """Search + Index wrapper for embedded mode."""

    def __init__(self, db: object, cfg: object, roots: list[str], settings_obj: object = None):
        self.db = db
        self.cfg = cfg
        self.settings = settings_obj or settings
        self.root_ids = [WorkspaceManager.root_id(r) for r in roots]
        self.runtime = EngineRuntime(roots, settings_obj=self.settings)
        self.runtime.initialize()
        self.search_engine = SearchEngine(
            db, tantivy_engine=self.runtime.engine)

    def status(self) -> EngineMeta:
        return self.runtime.status()

    def install(self) -> None:
        # Best-effort import check
        if self.runtime.engine is None:
            raise EngineError(
                "ERR_ENGINE_NOT_INSTALLED",
                "engine not installed",
                "sari --cmd engine install")

    def search(self, opts: object) -> SearchV2Result:
        return self.search_engine.search(opts)

    def repo_candidates(self, q: str, limit: int = 3,
                        root_ids: Optional[list[str]] = None) -> SearchRows:
        return self.search_engine.repo_candidates(
            q, limit=limit, root_ids=root_ids)

    def upsert_documents(self, docs: list[Doc], commit: bool = True) -> None:
        if self.runtime.engine:
            self.runtime.engine.upsert_documents(docs, commit=commit)

    def delete_documents(self, doc_ids: list[str], commit: bool = True) -> None:
        if self.runtime.engine:
            self.runtime.engine.delete_documents(doc_ids, commit=commit)

    def close(self) -> None:
        self.runtime.close()


class SqliteSearchEngineAdapter:
    """Adapter for sqlite-only search (no embedded engine)."""

    def __init__(self, db: object):
        self.db = db
        self.search_engine = SearchEngine(db, tantivy_engine=None)

    def status(self) -> EngineMeta:
        return EngineMeta(engine_mode="sqlite", engine_ready=True)

    def search(self, opts: object) -> SearchV2Result:
        return self.search_engine.search(opts)

    def repo_candidates(self, q: str, limit: int = 3,
                        root_ids: Optional[list[str]] = None) -> SearchRows:
        return self.search_engine.repo_candidates(
            q, limit=limit, root_ids=root_ids)

    def close(self) -> None:
        pass
