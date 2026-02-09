import sqlite3
import json
import difflib
import time
from typing import Iterable, List, Optional, Tuple, Dict, Any
from .base import BaseRepository
from ..parsers.common import _symbol_id
from ..models import SymbolDTO, _to_dict

# --- Symbol Column Master List ---
SYMBOL_COLUMNS = [
    "symbol_id", "path", "root_id", "name", "kind", "line", "end_line", 
    "content", "parent", "meta_json", "doc_comment", "qualname", "importance_score"
]

class SymbolRepository(BaseRepository):
    def upsert_symbols_tx(self, cur: sqlite3.Cursor, symbols: Iterable[Any]) -> int:
        """
        Robustly upsert symbols using named mapping to avoid index hell.
        """
        symbols_list = list(symbols)
        if not symbols_list:
            return 0

        normalized = []
        now = int(time.time())
        
        for s in symbols_list:
            # 1. Convert to dict regardless of input format (DTO, Tuple, or Dict)
            try:
                if hasattr(s, "model_dump"):
                    data = s.model_dump()
                elif isinstance(s, dict):
                    data = s
                else:
                    # Fallback for legacy tuple inputs (attempt best-effort mapping)
                    vals = list(s)
                    if len(vals) >= 12: # New format
                        data = dict(zip(SYMBOL_COLUMNS, vals))
                    else: # Old format fallback
                        data = {
                            "path": str(vals[0]), "name": str(vals[1]), "kind": str(vals[2]),
                            "line": int(vals[3] or 0), "end_line": int(vals[4] or 0),
                            "content": str(vals[5]), "parent": str(vals[6]),
                            "meta_json": str(vals[7] or "{}"), "doc_comment": str(vals[8] or ""),
                            "qualname": str(vals[9] or ""), "symbol_id": str(vals[10] or ""),
                            "root_id": str(vals[11]) if len(vals) > 11 else "root"
                        }
            except Exception:
                continue

            # 2. Fill defaults and ensure types
            name = str(data.get("name") or "")
            parent = str(data.get("parent") or "")
            qualname = str(data.get("qualname") or (f"{parent}.{name}" if parent else name))
            path = str(data.get("path") or "")
            kind = str(data.get("kind") or "unknown")
            
            row = {
                "symbol_id": str(data.get("symbol_id") or _symbol_id(path, kind, qualname)),
                "path": path,
                "root_id": str(data.get("root_id") or "root"),
                "name": name,
                "kind": kind,
                "line": int(data.get("line") or 0),
                "end_line": int(data.get("end_line") or 0),
                "content": str(data.get("content") or ""),
                "parent": parent,
                "meta_json": str(data.get("meta_json") or "{}"),
                "doc_comment": str(data.get("doc_comment") or ""),
                "qualname": qualname,
                "importance_score": float(data.get("importance_score") or 0.0)
            }
            normalized.append(tuple(row[col] for col in SYMBOL_COLUMNS))

        if not normalized: return 0

        # Group by path/root to clean up old symbols before insertion
        paths = {(r[1], r[2]) for r in normalized}
        for p, rid in paths:
            cur.execute("DELETE FROM symbols WHERE path = ? AND root_id = ?", (p, rid))

        col_names = ", ".join(SYMBOL_COLUMNS)
        placeholders = ",".join(["?"] * len(SYMBOL_COLUMNS))
        cur.executemany(f"INSERT OR REPLACE INTO symbols({col_names}) VALUES({placeholders})", normalized)
        return len(normalized)

    def upsert_relations_tx(self, cur: sqlite3.Cursor, relations: Iterable[tuple]) -> int:
        rels_list = list(relations)
        if not rels_list: return 0

        # Standard 11-column mapping for relations
        normalized = []
        for r in rels_list:
            vals = list(r)
            while len(vals) < 11:
                vals.append("") if len(vals) != 9 else vals.append(0) # line is int
            normalized.append(tuple(vals[:11]))

        cur.executemany(
            """
            INSERT OR REPLACE INTO symbol_relations(
                from_path, from_root_id, from_symbol, from_symbol_id, 
                to_path, to_root_id, to_symbol, to_symbol_id, 
                rel_type, line, meta_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            normalized,
        )
        return len(normalized)

    def get_symbol_range(self, path: str, name: str) -> Optional[Tuple[int, int]]:
        row = self.execute("SELECT line, end_line FROM symbols WHERE path = ? AND name = ? ORDER BY line ASC LIMIT 1", (path, name)).fetchone()
        return (int(row["line"]), int(row["end_line"])) if row else None

    def list_symbols_by_path(self, path: str) -> List[SymbolDTO]:
        rows = self.execute("SELECT s.*, f.repo FROM symbols s LEFT JOIN files f ON s.path = f.path WHERE s.path = ? ORDER BY s.line ASC", (path,)).fetchall()
        return [self._to_dto(r) for r in rows]

    def _to_dto(self, r) -> SymbolDTO:
        d = _to_dict(r)
        return SymbolDTO(
            symbol_id=d.get("symbol_id", ""), 
            path=d.get("path", ""), 
            root_id=d.get("root_id", ""), 
            repo=d.get("repo", ""), 
            name=d.get("name", ""), 
            kind=d.get("kind", ""), 
            line=d.get("line", 0), 
            end_line=d.get("end_line", 0), 
            content=d.get("content", ""),
            parent_name=d.get("parent", ""), 
            qualname=d.get("qualname", ""),
            metadata=json.loads(d["meta_json"]) if d.get("meta_json") else {}
        )

    def search_symbols(self, query: str, limit: int = 20, **kwargs) -> List[SymbolDTO]:
        lq = f"%{query}%"
        sql = "SELECT s.*, f.repo FROM symbols s LEFT JOIN files f ON s.path = f.path WHERE (s.name LIKE ? OR s.qualname LIKE ?)"
        params = [lq, lq]
        
        if kwargs.get("kinds"):
            ks = kwargs["kinds"]
            sql += f" AND s.kind IN ({','.join(['?']*len(ks))})"; params.extend(ks)
        if kwargs.get("root_ids"):
            rs = kwargs["root_ids"]
            sql += f" AND s.root_id IN ({','.join(['?']*len(rs))})"; params.extend(rs)
        if kwargs.get("repo"):
            sql += " AND f.repo = ?"; params.append(kwargs["repo"])
            
        sql += " ORDER BY s.importance_score DESC, s.name ASC LIMIT ?"
        params.append(limit)
        return [self._to_dto(r) for r in self.execute(sql, params).fetchall()]

    def fuzzy_search_symbols(self, query: str, limit: int = 5, min_score: float = 0.6) -> List[SymbolDTO]:
        if not query: return []
        
        # 1. SQL level pre-filtering: Get all unique names
        # For performance in large DBs, we'd use a trigram index or similar, 
        # but here we fetch candidates that share some characters.
        all_names = [r[0] for r in self.execute("SELECT name FROM symbols GROUP BY name").fetchall()]
        
        # 2. difflib refined matching
        matches = difflib.get_close_matches(query, all_names, n=limit, cutoff=min_score)
        if not matches: return []
        
        placeholders = ",".join(["?"] * len(matches))
        rows = self.execute(f"SELECT s.*, f.repo FROM symbols s LEFT JOIN files f ON s.path = f.path WHERE s.name IN ({placeholders}) ORDER BY s.importance_score DESC", matches).fetchall()
        return [self._to_dto(r) for r in rows]

    def recalculate_symbol_importance(self) -> int:
        self.execute("UPDATE symbols SET importance_score = 0.0")
        sql = """
            UPDATE symbols 
            SET importance_score = (
                SELECT COUNT(DISTINCT r.from_path) 
                FROM symbol_relations r 
                WHERE r.to_symbol_id = symbols.symbol_id OR (r.to_symbol = symbols.name AND (r.to_symbol_id IS NULL OR r.to_symbol_id = ''))
            )
        """
        self.execute(sql)
        res = self.execute("SELECT COUNT(1) FROM symbols WHERE importance_score > 0").fetchone()
        return res[0] if res else 0

    def get_symbol_fan_in_stats(self, symbol_names: List[str]) -> Dict[str, int]:
        if not symbol_names: return {}
        placeholders = ",".join(["?"] * len(symbol_names))
        sql = f"SELECT to_symbol, COUNT(1) FROM symbol_relations WHERE to_symbol IN ({placeholders}) GROUP BY to_symbol"
        rows = self.execute(sql, symbol_names).fetchall()
        return {r["to_symbol"]: r[1] for r in rows}
