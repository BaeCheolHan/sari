from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any, Union, Tuple
import json
import logging

logger = logging.getLogger("sari.models")

# --- Single Source of Truth for DB Column Ordering ---
# These must match schema.py EXACTLY.
FILE_COLUMNS = [
    "path",
    "rel_path",
    "root_id",
    "repo",
    "mtime",
    "size",
    "content",
    "hash",
    "fts_content",
    "last_seen_ts",
    "deleted_ts",
    "status",
    "error",
    "parse_status",
    "parse_error",
    "ast_status",
    "ast_reason",
    "is_binary",
    "is_minified",
    "metadata_json"]


def _to_dict(row: Any) -> Dict[str, Any]:
    if isinstance(row, dict):
        return row
    try:
        if hasattr(row, "keys"):
            return {k: row[k] for k in row.keys()}
    except Exception as e:
        logger.debug("Row to dict conversion failed: %s", e)

    if isinstance(row, (list, tuple)) and len(row) == len(FILE_COLUMNS):
        return dict(zip(FILE_COLUMNS, row))

    if hasattr(row, "__dict__"):
        return row.__dict__
    return {}


class SearchOptions(BaseModel):
    model_config = ConfigDict(frozen=True)
    query: str
    limit: int = 50
    offset: int = 0
    root_ids: Optional[List[str]] = None
    use_regex: bool = False
    case_sensitive: bool = False
    recency_boost: bool = False
    include_content: bool = False
    repo: Optional[str] = None
    file_types: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    path_pattern: Optional[str] = None
    snippet_lines: int = 3
    total_mode: str = "exact"


class SearchHit(BaseModel):
    repo: str = ""
    path: str = ""
    score: float = 0.0
    snippet: str = ""
    mtime: int = 0
    size: int = 0
    match_count: int = 1
    file_type: str = ""
    hit_reason: str = ""
    scope_reason: str = ""
    context_symbol: str = ""
    docstring: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SearchHit":
        return cls(**data)

    def to_result_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.path,
            "repo": self.repo,
            "path": self.path,
            "score": self.score,
            "snippet": self.snippet,
            "mtime": self.mtime,
            "size": self.size,
            "match_count": self.match_count,
            "file_type": self.file_type,
            "hit_reason": self.hit_reason,
            "scope_reason": self.scope_reason,
            "context_symbol": self.context_symbol,
            "docstring": self.docstring,
            "metadata": self.metadata,
        }


class FileDTO(BaseModel):
    path: str
    rel_path: str = ""
    root_id: str = ""
    repo: str = ""
    mtime: int = 0
    size: int = 0
    content: Optional[Union[str, bytes]] = None
    hash: str = ""
    fts_content: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Union[Dict, Any]) -> "FileDTO":
        d = _to_dict(row)
        meta = {}
        m_key = "metadata_json" if "metadata_json" in d else "meta_json"
        if d.get(m_key):
            try:
                meta = json.loads(d[m_key])
            except Exception as e:
                logger.debug("FileDTO meta parse error: %s", e)

        return cls(
            path=d.get("path", ""),
            rel_path=d.get("rel_path", ""),
            root_id=d.get("root_id", ""),
            repo=d.get("repo", ""),
            mtime=int(d.get("mtime", 0)),
            size=int(d.get("size", 0)),
            content=d.get("content"),
            hash=d.get("hash", d.get("content_hash", "")),
            fts_content=d.get("fts_content", ""),
            metadata=meta
        )


class SymbolDTO(BaseModel):
    symbol_id: str = ""
    path: str
    root_id: str = ""
    repo: str = ""
    name: str
    kind: str
    line: int
    end_line: int = 0
    content: str = ""
    parent_name: Optional[str] = ""
    qualname: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Union[Dict, Any]) -> "SymbolDTO":
        d = _to_dict(row)
        meta = {}
        m_key = "meta_json" if "meta_json" in d else "metadata"
        if d.get(m_key):
            try:
                m = d[m_key]
                meta = json.loads(m) if isinstance(m, str) else m
            except Exception as e:
                logger.debug("SymbolDTO meta parse error: %s", e)

        return cls(
            symbol_id=d.get("symbol_id", d.get("sid", "")),
            path=d.get("path", ""),
            root_id=d.get("root_id", ""),
            repo=d.get("repo", ""),
            name=d.get("name", ""),
            kind=d.get("kind", ""),
            line=int(d.get("line", 0)),
            end_line=int(d.get("end_line", 0)),
            content=d.get("content", ""),
            parent_name=d.get("parent", d.get("parent_name", "")),
            qualname=d.get("qualname", ""),
            metadata=meta
        )


class SnippetDTO(BaseModel):
    id: Optional[int] = None
    tag: str
    path: str
    root_id: str
    start_line: int
    end_line: int
    content: str
    content_hash: str = ""
    anchor_before: str = ""
    anchor_after: str = ""
    repo: str = ""
    note: str = ""
    commit_hash: str = ""
    created_ts: int = 0
    updated_ts: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Union[Dict, Any]) -> "SnippetDTO":
        d = _to_dict(row)
        meta = {}
        m_key = "metadata_json" if "metadata_json" in d else "meta_json"
        if d.get(m_key):
            try:
                meta = json.loads(d[m_key])
            except Exception as e:
                logger.debug("SnippetDTO meta parse error: %s", e)
        return cls(
            id=d.get("id"),
            tag=d.get("tag", ""),
            path=d.get("path", ""),
            root_id=d.get("root_id", ""),
            start_line=int(d.get("start_line", 0)),
            end_line=int(d.get("end_line", 0)),
            content=d.get("content", ""),
            content_hash=d.get("content_hash", ""),
            anchor_before=d.get("anchor_before", ""),
            anchor_after=d.get("anchor_after", ""),
            repo=d.get("repo", ""),
            note=d.get("note", ""),
            commit_hash=d.get("commit_hash", ""),
            created_ts=int(d.get("created_ts", 0)),
            updated_ts=int(d.get("updated_ts", 0)),
            metadata=meta
        )


class IndexingResult(BaseModel):
    type: str = "changed"
    path: str
    rel: str
    root_id: str = "root"
    repo: str = ""
    mtime: int = 0
    size: int = 0
    content: Optional[Union[str, bytes]] = None
    content_hash: str = ""
    fts_content: str = ""
    scan_ts: int = 0
    parse_status: str = "ok"
    parse_reason: str = "none"
    ast_status: str = "skipped"
    ast_reason: str = ""
    is_binary: int = 0
    is_minified: int = 0
    content_bytes: int = 0
    metadata_json: str = "{}"
    symbols: List[Any] = Field(default_factory=list)
    relations: List[Any] = Field(default_factory=list)
    engine_doc: Optional[Dict[str, Any]] = None

    def to_file_row(self) -> Tuple:
        """Dynamically build the tuple using FILE_COLUMNS SSOT to eliminate magic numbers."""
        data = {
            "path": self.path,
            "rel_path": self.rel,
            "root_id": self.root_id,
            "repo": self.repo,
            "mtime": self.mtime,
            "size": self.size,
            "content": self.content,
            "hash": self.content_hash,
            "fts_content": self.fts_content,
            "last_seen_ts": self.scan_ts,
            "deleted_ts": 0,
            "status": "ok",
            "error": None,
            "parse_status": self.parse_status,
            "parse_error": self.parse_reason,
            "ast_status": self.ast_status,
            "ast_reason": self.ast_reason,
            "is_binary": self.is_binary,
            "is_minified": self.is_minified,
            "metadata_json": self.metadata_json
        }
        return tuple(data.get(col) for col in FILE_COLUMNS)


class ContextDTO(BaseModel):

    id: Optional[int] = None

    topic: str

    content: str

    tags: List[str] = Field(default_factory=list)

    related_files: List[str] = Field(default_factory=list)

    source: str = ""

    valid_from: int = 0

    valid_until: int = 0

    deprecated: bool = False

    created_ts: int = 0

    updated_ts: int = 0

    @classmethod
    def from_row(cls, row: Union[Dict, Any]) -> "ContextDTO":

        d = _to_dict(row)

        def _parse_json_list(key):

            val = d.get(key)

            if not val:
                return []

            if isinstance(val, list):
                return val

            try:
                return json.loads(val)

            except Exception as e:

                logger.debug(
                    "ContextDTO json list parse error for %s: %s", key, e)

                return []

        return cls(

            id=d.get("id"),

            topic=d.get("topic", ""),

            content=d.get("content", ""),

            tags=_parse_json_list("tags_json"),

            related_files=_parse_json_list("related_files_json"),

            source=d.get("source", ""),

            valid_from=int(d.get("valid_from", 0)),

            valid_until=int(d.get("valid_until", 0)),

            deprecated=bool(d.get("deprecated", 0)),

            created_ts=int(d.get("created_ts", 0)),

            updated_ts=int(d.get("updated_ts", 0))

        )


# --- Parser Result Objects (to avoid messy tuple indexing) ---


class ParserSymbol(BaseModel):

    sid: str

    path: str

    name: str

    kind: str

    line: int

    end_line: int

    content: str

    parent: str = ""

    meta: Dict[str, Any] = Field(default_factory=dict)

    qualname: str = ""

    doc: str = ""


class ParserRelation(BaseModel):

    from_name: str

    from_sid: str

    to_name: str

    to_sid: str = ""

    rel_type: str

    line: int

    meta: Dict[str, Any] = Field(default_factory=dict)

    to_path: str = ""
