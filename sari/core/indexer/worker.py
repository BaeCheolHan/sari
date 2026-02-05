import os
import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from sari.core.cjk import has_cjk as _has_cjk
from sari.core.settings import settings
from sari.core.parsers.factory import ParserFactory
from sari.core.parsers.ast_engine import ASTEngine
import hashlib
import zlib
from sari.core.utils import _redact, _sample_file, _printable_ratio, _is_minified, _normalize_engine_text

def compute_hash(content: str) -> str:
    """Compute SHA-1 hash for delta indexing."""
    return hashlib.sha1(content.encode("utf-8", errors="ignore")).hexdigest()

class IndexWorker:
    def __init__(self, cfg, db, logger, extractor_cb, settings_obj=None):
        self.cfg = cfg
        self.db = db
        self.logger = logger
        self.extractor_cb = extractor_cb
        self.settings = settings_obj or settings
        self.ast_engine = ASTEngine()
        self._ast_cache = OrderedDict()
        self._ast_cache_max = self.settings.AST_CACHE_ENTRIES

    def process_file_task(self, root: Path, file_path: Path, st: os.stat_result, scan_ts: int, now: float, excluded: bool, root_id: Optional[str] = None) -> Optional[dict]:
        try:
            rel_to_root = str(file_path.relative_to(root))
            db_path = self._encode_db_path(root, file_path, root_id=root_id)
            repo = rel_to_root.split(os.sep, 1)[0] if os.sep in rel_to_root else "__root__"
            ext = file_path.suffix.lower()

            # 1. Fast Metadata Check
            prev = self.db.get_file_meta(db_path)
            # prev: (mtime, size, content_hash)
            if prev and int(st.st_mtime) == int(prev[0]) and int(st.st_size) == int(prev[1]):
                return {"type": "unchanged", "rel": db_path}

            # 2. Content-based Delta Check
            size = st.st_size
            if size > self.settings.MAX_PARSE_BYTES:
                return self._skip_result(db_path, repo, st, scan_ts, "too_large")

            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if not content:
                return self._skip_result(db_path, repo, st, scan_ts, "empty")

            current_hash = compute_hash(content)
            if prev and prev[2] == current_hash:
                # Content hasn't changed despite mtime update
                return {"type": "unchanged", "rel": db_path}

            # 3. Actual Processing (Changed or New)
            if self.settings.get_bool("REDACT_ENABLED", True):
                content = _redact(content)
            normalized = _normalize_engine_text(content)
            
            # Truncate for FTS index stability
            fts_max = self.settings.get_int("FTS_MAX_BYTES", 1000000)
            fts_content = normalized[:fts_max] if normalized else ""

            symbols, relations = [], []
            ast_status, ast_reason = "skipped", "disabled"
            if size <= self.settings.MAX_AST_BYTES:
                try:
                    symbols, relations = self.extractor_cb(db_path, content)
                except:
                    pass
                if self.ast_engine.enabled:
                    lang = ParserFactory.get_language(ext)
                    if lang:
                        old_tree = self._ast_cache_get(db_path)
                        tree = self.ast_engine.parse(lang, content, old_tree)
                        if tree:
                            ast_status, ast_reason = "ok", "none"
                            self._ast_cache_put(db_path, tree)
                            try:
                                ts_symbols = self.ast_engine.extract_symbols(db_path, lang, content, tree=tree)
                                if ts_symbols:
                                    symbols = self._merge_symbols(symbols, ts_symbols)
                            except Exception:
                                pass
                        else:
                            ast_status, ast_reason = "failed", "parse_error"
                    else:
                        ast_status, ast_reason = "skipped", "unsupported"
            else:
                ast_status, ast_reason = "skipped", "too_large"

            store_content = getattr(self.cfg, "store_content", True)
            stored_content = content if store_content else ""
            metadata_json = "{}"
            content_bytes = len(stored_content)
            if not store_content:
                metadata_json = '{"stored":false}'
            elif self.settings.STORE_CONTENT_COMPRESS and stored_content:
                level = max(1, min(9, self.settings.STORE_CONTENT_COMPRESS_LEVEL))
                raw_bytes = stored_content.encode("utf-8", errors="ignore")
                comp = zlib.compress(raw_bytes, level)
                stored_content = b"ZLIB\0" + comp
                content_bytes = len(raw_bytes)
                metadata_json = f'{{"compressed":"zlib","orig_bytes":{content_bytes}}}'
            fts_content = normalized
            try:
                max_fts = int(self.settings.FTS_MAX_BYTES)
                if max_fts > 0 and len(fts_content) > max_fts:
                    fts_content = fts_content[:max_fts]
            except Exception:
                pass
            return {
                "type": "changed", "rel": db_path, "repo": repo, "mtime": int(st.st_mtime), "size": size,
                "content": stored_content, "content_hash": current_hash, "scan_ts": scan_ts,
                "fts_content": fts_content,
                "content_bytes": content_bytes, "metadata_json": metadata_json,
                "fts_content": fts_content,
                "symbols": symbols, "relations": relations,
                "parse_status": "ok", "parse_reason": "none",
                "ast_status": ast_status, "ast_reason": ast_reason,
                "is_binary": 0, "is_minified": 0,
                "engine_doc": self._build_engine_doc(db_path, repo, rel_to_root, normalized, int(st.st_mtime), size)
            }
        except Exception as e:
            if self.logger: self.logger.log_error(f"Worker failed for {file_path}: {e}")
            return None

    def _skip_result(self, db_path, repo, st, scan_ts, reason):
        return {
            "type": "changed", "rel": db_path, "repo": repo, "mtime": int(st.st_mtime), "size": st.st_size,
            "content": "", "scan_ts": scan_ts, "symbols": [], "relations": [],
            "parse_status": "skipped", "parse_reason": reason,
            "ast_status": "skipped", "ast_reason": reason,
            "is_binary": 1 if reason == "binary" else 0,
            "engine_doc": None
        }

    def _build_engine_doc(self, doc_id, repo, rel_to_root, normalized_content, mtime, size):
        norm = normalized_content or ""
        root_id = doc_id.split("/", 1)[0] if "/" in doc_id else ""
        return {
            "doc_id": doc_id, "repo": repo, "rel_path": rel_to_root,
            "root_id": root_id,
            "body_text": norm[:self.settings.ENGINE_MAX_DOC_BYTES],
            "mtime": mtime, "size": size
        }

    def _encode_db_path(self, root: Path, file_path: Path, root_id: Optional[str] = None) -> str:
        if not root_id:
            from sari.core.workspace import WorkspaceManager
            root_id = WorkspaceManager.root_id(str(root))
        rel = file_path.relative_to(root).as_posix()
        return f"{root_id}/{rel}"

    def _merge_symbols(self, base: List[Tuple], extra: List[Tuple]) -> List[Tuple]:
        seen = set()
        out: List[Tuple] = []
        for s in base + extra:
            key = (s[1], s[2], s[3], s[4])
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out

    def _ast_cache_get(self, path: str):
        if self._ast_cache_max <= 0:
            return None
        tree = self._ast_cache.get(path)
        if tree is not None:
            self._ast_cache.move_to_end(path)
        return tree

    def _ast_cache_put(self, path: str, tree: Any) -> None:
        if self._ast_cache_max <= 0:
            return
        self._ast_cache[path] = tree
        self._ast_cache.move_to_end(path)
        while len(self._ast_cache) > self._ast_cache_max:
            self._ast_cache.popitem(last=False)
