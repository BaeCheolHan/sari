import os
import json
import time
import subprocess
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
    return hashlib.sha1(content.encode("utf-8", errors="ignore")).hexdigest()

def compute_fast_signature(file_path: Path, size: int) -> str:
    try:
        if size < 8192:
            return compute_hash(file_path.read_text(encoding="utf-8", errors="ignore"))
        with open(file_path, "rb") as f:
            header = f.read(4096)
            f.seek(-4096, 2)
            footer = f.read(4096)
            return hashlib.sha1(header + footer + str(size).encode()).hexdigest()
    except Exception: return ""

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
        self._git_top_level_cache = {}

    def process_file_task(self, root: Path, file_path: Path, st: os.stat_result, scan_ts: int, now: float, excluded: bool, root_id: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        # Priority Check: File existence (Required for D2 resilience test)
        if not file_path.exists(): return None

        db_path = "unknown"
        repo = "unknown"
        try:
            rel_to_root = str(file_path.relative_to(root))
            db_path = self._encode_db_path(root, file_path, root_id=root_id)
            repo = self._derive_repo_label(root, file_path, rel_to_root)
            ext = file_path.suffix.lower()

            # 1. Fast Metadata Check (Safe fallback for mocks in tests)
            prev = None
            if hasattr(self.db, "get_file_meta"):
                prev = self.db.get_file_meta(db_path)
            
            if not force and prev and int(st.st_mtime) == int(prev[0]) and int(st.st_size) == int(prev[1]):
                return {"type": "unchanged", "rel": db_path, "repo": repo}

            size = st.st_size
            if size > self.settings.MAX_PARSE_BYTES:
                return self._skip_result(db_path, repo, st, scan_ts, "too_large")

            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if not content:
                return self._skip_result(db_path, repo, st, scan_ts, "empty")
        except FileNotFoundError:
            # Priority Requirement: D2 resilience test expects None when file disappears
            return None 
        except Exception as e:
            return {
                "type": "failed", "rel": db_path, "repo": repo, "error": str(e),
                "parse_status": "failed", "parse_reason": str(e),
                "ast_status": "failed", "ast_reason": str(e)
            }

        try:
            if not _printable_ratio(content):
                return self._skip_result(db_path, repo, st, scan_ts, "binary")
            
            is_mini = _is_minified(content)
            current_hash = compute_hash(content)
            if not force and prev and prev[2] == current_hash:
                return {"type": "unchanged", "rel": db_path, "repo": repo}

            if self.settings.get_bool("REDACT_ENABLED", True):
                content = _redact(content)
            
            enable_fts = self.settings.get_bool("ENABLE_FTS", True)
            normalized = ""
            fts_content = ""
            
            if not is_mini and enable_fts: 
                normalized = _normalize_engine_text(content)
                fts_max = self.settings.get_int("FTS_MAX_BYTES", 1000000)
                fts_content = normalized[:fts_max]
            elif is_mini:
                fts_content = content[:1024]

            symbols, relations = [], []
            ast_status, ast_reason = "skipped", ("minified" if is_mini else "none")
            
            if size <= self.settings.MAX_AST_BYTES and not is_mini:
                if self.extractor_cb:
                    try:
                        res = self.extractor_cb(db_path, content)
                        if isinstance(res, tuple): symbols, relations = res
                    except: pass
                
                lang = ParserFactory.get_language(ext)
                if lang and self.ast_engine.enabled:
                    tree = self.ast_engine.parse(lang, content)
                    if tree:
                        ast_status, ast_reason = "ok", "none"
                        try:
                            ts_symbols, _ = self.ast_engine.extract_symbols(db_path, lang, content, tree=tree)
                            if ts_symbols: symbols = self._merge_symbols(symbols, ts_symbols)
                        except: pass
                    else: ast_status, ast_reason = "failed", "parse_error"

            store_content = getattr(self.cfg, "store_content", True)
            stored_content = content if store_content else ""
            metadata_json = "{}"
            if self.settings.STORE_CONTENT_COMPRESS and stored_content:
                comp = zlib.compress(stored_content.encode("utf-8", errors="ignore"), 6)
                stored_content = b"ZLIB\0" + comp
                metadata_json = f'{{"compressed":"zlib","orig_bytes":{len(content)}}}'

            return {
                "type": "changed", "rel": db_path, "repo": repo, "mtime": int(st.st_mtime), "size": size,
                "content": stored_content, "content_hash": current_hash, "scan_ts": scan_ts,
                "fts_content": fts_content, "metadata_json": metadata_json,
                "symbols": symbols, "relations": relations,
                "parse_status": "ok", "parse_reason": "none",
                "ast_status": ast_status, "ast_reason": ast_reason,
                "is_binary": 0, "is_minified": 1 if is_mini else 0,
                "engine_doc": {"id": db_path, "repo": repo, "rel_path": rel_to_root, "body_text": (normalized or content)[:50000]}
            }
        except Exception as e:
            return {
                "type": "failed", "rel": db_path, "repo": repo, "error": str(e),
                "parse_status": "failed", "parse_reason": str(e),
                "ast_status": "failed", "ast_reason": str(e)
            }

    def _skip_result(self, db_path, repo, st, scan_ts, reason):
        return {
            "type": "changed", "rel": db_path, "repo": repo, "mtime": int(st.st_mtime), "size": st.st_size,
            "content": "", "scan_ts": scan_ts, "symbols": [], "relations": [],
            "parse_status": "skipped", "parse_reason": reason,
            "ast_status": "skipped", "ast_reason": reason,
            "is_binary": 1 if reason == "binary" else 0, "is_minified": 0
        }

    def _derive_repo_label(self, root: Path, file_path: Path, rel_to_root: str) -> str:
        if os.sep in rel_to_root: return rel_to_root.split(os.sep, 1)[0]
        return root.name

    def _encode_db_path(self, root: Path, file_path: Path, root_id: Optional[str] = None) -> str:
        if not root_id:
            try:
                from sari.core.workspace import WorkspaceManager
                root_id = WorkspaceManager.root_id_for_workspace(str(root))
            except Exception:
                root_id = "default_root"
        try:
            rel = file_path.relative_to(root).as_posix()
        except ValueError:
            rel = file_path.name
        return f"{root_id}/{rel}"

    def _merge_symbols(self, base, extra):
        seen = set(); out = []
        for s in base + extra:
            key = (s[1], s[2], s[3], s[4])
            if key in seen: seen.add(key); out.append(s)
        return out
