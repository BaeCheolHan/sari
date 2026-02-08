from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
import time

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

class RepoStat(BaseModel):
    repo: str
    file_count: int
    last_updated: int = Field(default_factory=lambda: int(time.time()))

class SymbolModel(BaseModel):
    name: str = ""
    kind: str = ""
    line: int = 0
    end_line: int = 0
    path: str = ""
    root_id: str = ""
    qualname: Optional[str] = ""
