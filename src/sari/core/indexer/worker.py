import os
import json
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional
from sari.core.settings import settings
from sari.core.parsers.factory import ParserFactory
from sari.core.parsers.ast_engine import ASTEngine
from sari.core.utils.path import PathUtils
import hashlib
import zlib
from sari.core.utils import _redact, _normalize_engine_text
from sari.core.models import IndexingResult


def compute_hash(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8", errors="ignore")).hexdigest()


def compute_fast_signature(file_path: Path, size: int) -> str:
    try:
        if size < 8192:
            return compute_hash(
                file_path.read_text(
                    encoding="utf-8",
                    errors="ignore"))
        with open(file_path, "rb") as f:
            header = f.read(4096)
            f.seek(-4096, 2)
            footer = f.read(4096)
            return hashlib.sha1(
                header + footer + str(size).encode()).hexdigest()
    except Exception:
        return ""


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
        self._git_root_cache: Dict[str, Optional[str]] = {}

    def process_file_task(
            self,
            root: Path,
            file_path: Path,
            st: os.stat_result,
            scan_ts: int,
            now: float,
            excluded: bool,
            root_id: Optional[str] = None,
            force: bool = False) -> Optional[IndexingResult]:
        try:
            db_path = self._encode_db_path(root, file_path, root_id=root_id)
            rel_to_root = PathUtils.to_relative(str(file_path), str(root))

            # 1. Delta Check (Metadata)
            prev = self.db.get_file_meta(db_path)
            if not force and prev and int(
                st.st_mtime) == int(
                prev[0]) and int(
                st.st_size) == int(
                    prev[1]):
                return IndexingResult(
                    type="unchanged", path=str(file_path), rel=db_path)

            # 2. Fast Signature Check
            size = st.st_size
            if not force and prev and size == int(prev[1]):
                sig = compute_fast_signature(file_path, size)
                if sig and sig == prev[2]:
                    return IndexingResult(
                        type="unchanged", path=str(file_path), rel=db_path)

            if size > self.settings.MAX_PARSE_BYTES:
                return self._skip_result(
                    db_path,
                    str(file_path),
                    st,
                    scan_ts,
                    "too_large",
                    root_id=root_id)

            # 3. Content Reading & Redaction
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if not content:
                return self._skip_result(
                    db_path,
                    str(file_path),
                    st,
                    scan_ts,
                    "empty",
                    root_id=root_id)

            current_hash = compute_hash(content)
            if prev and prev[2] == current_hash:
                return IndexingResult(
                    type="unchanged", path=str(file_path), rel=db_path)

            repo = self._derive_repo_label(root, file_path, rel_to_root)
            if self.settings.get_bool("REDACT_ENABLED", True):
                content = _redact(content)

            # 4. FTS and AST Processing
            enable_fts = self.settings.get_bool("ENABLE_FTS", True)
            normalized = _normalize_engine_text(content) if enable_fts else ""
            fts_content = normalized[:self.settings.get_int(
                "FTS_MAX_BYTES", 1000000)] if normalized else ""

            symbols, relations = [], []
            ast_status, ast_reason = "skipped", "disabled"
            if size <= self.settings.MAX_AST_BYTES and self.ast_engine.enabled:
                lang = ParserFactory.get_language(file_path.suffix.lower())
                if lang:
                    tree = self.ast_engine.parse(
                        lang, content, self._ast_cache_get(db_path))
                    if tree:
                        ast_status, ast_reason = "ok", "none"
                        self._ast_cache_put(db_path, tree)
                        ts_syms, ts_rels = self.ast_engine.extract_symbols(
                            db_path, lang, content, tree=tree)
                        symbols = ts_syms or []
                        relations = ts_rels or []
                    else:
                        ast_status, ast_reason = "failed", "parse_error"

            # 5. Storage & Result Assembly
            store_content = getattr(self.cfg, "store_content", True)
            stored_content = content if store_content else ""
            metadata = {"stored": store_content}

            if store_content and self.settings.STORE_CONTENT_COMPRESS:
                raw_bytes = stored_content.encode("utf-8", errors="ignore")
                stored_content = b"ZLIB\0" + \
                    zlib.compress(raw_bytes, self.settings.STORE_CONTENT_COMPRESS_LEVEL)
                metadata["compressed"] = "zlib"
                metadata["orig_bytes"] = len(raw_bytes)

            return IndexingResult(
                type="changed",
                path=str(file_path),
                rel=db_path,
                repo=repo,
                mtime=int(
                    st.st_mtime),
                size=size,
                content=stored_content,
                content_hash=current_hash,
                scan_ts=scan_ts,
                fts_content=fts_content,
                content_bytes=len(stored_content) if isinstance(
                    stored_content,
                    bytes) else len(
                    str(stored_content)),
                metadata_json=json.dumps(metadata),
                symbols=symbols,
                relations=relations,
                parse_status="ok",
                ast_status=ast_status,
                ast_reason=ast_reason,
                root_id=root_id or "root",
                engine_doc=self._build_engine_doc(
                    db_path,
                    repo,
                    rel_to_root,
                    normalized,
                    int(
                        st.st_mtime),
                    size))
        except Exception as e:
            if isinstance(
                    e, (FileNotFoundError, OSError)) and getattr(
                    e, "errno", None) == 2:
                return None
            if self.logger:
                self.logger.error(
                    f"Worker failure: {file_path} -> {e}",
                    exc_info=True)
            return None

    def _derive_repo_label(
            self,
            root: Path,
            file_path: Path,
            rel_to_root: str) -> str:
        # Optimization: Check if we already found a repo for this workspace
        # root
        root_path = Path(root)
        root_str = str(root_path.resolve())
        if root_str in self._git_root_cache:
            res = self._git_root_cache[root_str]
            if res:
                return res

        parent = str(file_path.parent.resolve())
        if parent in self._git_root_cache:
            git_root = self._git_root_cache[parent]
        else:
            try:
                # Fast path: check if .git exists in parent or its parents up
                # to root
                curr = Path(parent)
                found_git = False
                target_root = root_path.resolve()
                while curr.parts and str(curr).startswith(str(target_root)):
                    if (curr / ".git").is_dir():
                        found_git = True
                        git_root = str(curr)
                        break
                    curr = curr.parent

                if not found_git:
                    # Fallback to git command for complex cases (e.g.
                    # submodule, worktree)
                    proc = subprocess.run(["git",
                                           "-C",
                                           parent,
                                           "rev-parse",
                                           "--show-toplevel"],
                                          capture_output=True,
                                          text=True,
                                          check=False,
                                          timeout=0.5)
                    git_root = proc.stdout.strip() if proc.returncode == 0 else None
            except Exception:
                git_root = None
            self._git_root_cache[parent] = git_root

        if git_root:
            repo_name = Path(git_root).name
            # Cache for this workspace root
            self._git_root_cache[root_str] = repo_name
            return repo_name
        return Path(root).name or "root"

    def _skip_result(
            self,
            db_path,
            path,
            st,
            scan_ts,
            reason,
            root_id=None) -> IndexingResult:
        return IndexingResult(
            type="changed",
            path=path,
            rel=db_path,
            mtime=int(
                st.st_mtime),
            size=st.st_size,
            scan_ts=scan_ts,
            parse_status="skipped",
            parse_reason=reason,
            is_binary=1 if reason == "binary" else 0,
            root_id=root_id or "root")

    def _build_engine_doc(
            self,
            doc_id,
            repo,
            rel_to_root,
            normalized_content,
            mtime,
            size):
        norm = normalized_content or ""
        root_id = doc_id.split("/", 1)[0] if "/" in doc_id else ""
        return {
            "id": doc_id, "repo": repo, "rel_path": rel_to_root,
            "root_id": root_id,
            "body_text": norm[:self.settings.ENGINE_MAX_DOC_BYTES],
            "mtime": mtime, "size": size
        }

    def _encode_db_path(self, root: Path, file_path: Path,
                        root_id: Optional[str] = None) -> str:
        if not root_id:
            try:
                from sari.core.workspace import WorkspaceManager
                root_id = WorkspaceManager.root_id_for_workspace(str(root))
            except Exception:
                root_id = "default_root"

        # Use PathUtils for safe relative calculation
        rel = PathUtils.to_relative(str(file_path), str(root))
        if not rel:
            rel = file_path.name

        return f"{root_id}/{rel}"

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
