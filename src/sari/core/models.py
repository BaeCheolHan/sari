from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any, Union, Tuple
import time
import json

class SearchOptions(BaseModel):
    model_config = ConfigDict(frozen=True)
    query: str
    limit: int = 50
    root_ids: Optional[List[str]] = None
    use_regex: bool = False
    include_content: bool = False
    repo: Optional[str] = None
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
    content_hash: str = ""
    fts_content: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Union[Dict, Any]) -> "FileDTO":
        """Factory method to create DTO from DB row (sqlite3.Row or dict)."""
        d = dict(row) if not isinstance(row, dict) else row
        meta = {}
        if d.get("metadata_json"):
            try: meta = json.loads(d["metadata_json"])
            except: pass
        
        return cls(
            path=d.get("path", ""),
            rel_path=d.get("rel_path", ""),
            root_id=d.get("root_id", ""),
            repo=d.get("repo", ""),
            mtime=int(d.get("mtime", 0)),
            size=int(d.get("size", 0)),
            content=d.get("content"),
            content_hash=d.get("content_hash", ""),
            fts_content=d.get("fts_content", ""),
            metadata=meta
        )

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

class SymbolDTO(BaseModel):
    symbol_id: str = ""
    path: str
    root_id: str = ""
    name: str
    kind: str
    line: int
    end_line: int = 0
    content: str = ""
    parent_name: str = ""
    qualname: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Union[Dict, Any]) -> "SymbolDTO":
        d = dict(row) if not isinstance(row, dict) else row
        meta = {}
        if d.get("metadata"):
            try: 
                m = d["metadata"]
                meta = json.loads(m) if isinstance(m, str) else m
            except: pass
            
        return cls(
            symbol_id=d.get("symbol_id", ""),
            path=d.get("path", ""),
            root_id=d.get("root_id", ""),
            name=d.get("name", ""),
            kind=d.get("kind", ""),
            line=int(d.get("line", 0)),
            end_line=int(d.get("end_line", 0)),
            content=d.get("content", ""),
            parent_name=d.get("parent_name", ""),
            qualname=d.get("qualname", ""),
            metadata=meta
        )

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

class RepoStat(BaseModel):
    repo: str
    file_count: int
    last_updated: int = Field(default_factory=lambda: int(time.time()))

class SymbolModel(BaseModel):
    # Deprecated in favor of SymbolDTO, keeping for transition
    name: str = ""
    kind: str = ""
    line: int = 0
    end_line: int = 0
    path: str = ""
    root_id: str = ""
    qualname: Optional[str] = ""

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
        d = dict(row) if not isinstance(row, dict) else row
        meta = {}
        if d.get("metadata_json"):
            try: meta = json.loads(d["metadata_json"])
            except: pass
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
    """Result of a single file processing task."""
    type: str = "changed" # unchanged, changed, new, deleted, skipped
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
        """Convert to a 20-column tuple matching the 'files' table schema."""
        return (
            self.path,
            self.rel,
            self.root_id,
            self.repo,
            self.mtime,
            self.size,
            self.content,
            self.content_hash,
            self.fts_content,
            self.scan_ts,
            0, # deleted_ts
            self.parse_status,
            self.parse_reason,
            self.ast_status,
            self.ast_reason,
            self.is_binary,
            self.is_minified,
            0, # unused
            self.content_bytes,
            self.metadata_json
        )

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
        d = dict(row) if not isinstance(row, dict) else row
        
        def _parse_json_list(key):
            val = d.get(key)
            if not val: return []
            if isinstance(val, list): return val
            try: return json.loads(val)
            except: return []

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
