import sqlite3
from typing import Iterable, List, Optional, Tuple, Dict, Any
from .base import BaseRepository
from ..parsers.common import _symbol_id
from ..models import SymbolDTO

class SymbolRepository(BaseRepository):
    def upsert_symbols_tx(self, cur: sqlite3.Cursor, symbols: Iterable[tuple]) -> int:
        if hasattr(symbols, "symbols"):
            symbols_list = list(getattr(symbols, "symbols"))
        else:
            symbols_list = list(symbols)

        if not symbols_list:
            return 0

        normalized = []
        for s in symbols_list:
            vals = list(s)
            while len(vals) < 8:
                vals.append("")
            if len(vals) < 9:
                vals.append("{}")
            if len(vals) < 10:
                vals.append("")
            if len(vals) < 11:
                vals.append("")

            if len(vals) >= 12 and ("/" in str(vals[1]) or "\\" in str(vals[1])):
                # Schema format: (symbol_id, path, root_id, name, kind, line, end_line, content, parent_name, metadata, docstring, qualname)
                symbol_id = str(vals[0])
                path = str(vals[1])
                root_id = str(vals[2])
                name = str(vals[3])
                kind = str(vals[4])
                line = int(vals[5] or 0)
                end_line = int(vals[6] or 0)
                content = str(vals[7])
                parent_name = str(vals[8])
                metadata = str(vals[9] or "{}")
                docstring = str(vals[10])
                qualname = str(vals[11]) if vals[11] else (f"{parent_name}.{name}" if parent_name else name)
            else:
                # Legacy format: (path, name, kind, line, end_line, content, parent, metadata, docstring, qualname, symbol_id, root_id)
                path = str(vals[0])
                name = str(vals[1])
                kind = str(vals[2])
                line = int(vals[3] or 0)
                end_line = int(vals[4] or 0)
                content = str(vals[5])
                parent_name = str(vals[6])
                metadata = str(vals[7] or "{}")
                docstring = str(vals[8] or "")
                qualname = str(vals[9]) if vals[9] else (f"{parent_name}.{name}" if parent_name else name)
                symbol_id = str(vals[10] or _symbol_id(path, kind, qualname))
                root_id = str(vals[11]) if len(vals) > 11 else ""

            normalized.append(
                (
                    symbol_id,
                    path,
                    root_id,
                    name,
                    kind,
                    line,
                    end_line,
                    content,
                    parent_name,
                    metadata,
                    docstring,
                    qualname,
                )
            )

        paths = {(s[1], s[2]) for s in normalized}
        cur.executemany("DELETE FROM symbols WHERE path = ? AND root_id = ?", list(paths))
        cur.executemany(
            """
            INSERT OR REPLACE INTO symbols(symbol_id, path, root_id, name, kind, line, end_line, content, parent_name, metadata, docstring, qualname)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            normalized,
        )
        return len(normalized)

    def upsert_relations_tx(self, cur: sqlite3.Cursor, relations: Iterable[tuple]) -> int:
        rels_list = list(relations)
        if not rels_list:
            return 0

        normalized = []
        for r in rels_list:
            vals = list(r)
            if len(vals) < 10:
                vals = vals + [""] * (10 - len(vals))
            if len(vals) >= 10:
                (
                    from_path,
                    from_root_id,
                    from_symbol,
                    from_symbol_id,
                    to_path,
                    to_root_id,
                    to_symbol,
                    to_symbol_id,
                    rel_type,
                    line,
                ) = vals[:10]
            else:
                from_path, from_symbol, to_path, to_symbol, rel_type, line = vals[:6]
                from_root_id = ""
                to_root_id = ""
                from_symbol_id = ""
                to_symbol_id = ""
            normalized.append(
                (
                    from_path,
                    from_root_id,
                    from_symbol,
                    from_symbol_id,
                    to_path,
                    to_root_id,
                    to_symbol,
                    to_symbol_id,
                    rel_type,
                    int(line or 0),
                )
            )

        paths = {(r[0], r[1]) for r in normalized}
        cur.executemany("DELETE FROM symbol_relations WHERE from_path = ? AND from_root_id = ?", list(paths))
        cur.executemany(
            """
            INSERT OR REPLACE INTO symbol_relations(from_path, from_root_id, from_symbol, from_symbol_id, to_path, to_root_id, to_symbol, to_symbol_id, rel_type, line)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            normalized,
        )
        return len(normalized)

    def get_symbol_range(self, path: str, name: str) -> Optional[Tuple[int, int]]:
        row = self.execute(
            "SELECT line, end_line FROM symbols WHERE path = ? AND name = ? ORDER BY line ASC LIMIT 1",
            (path, name),
        ).fetchone()
        if not row:
            return None
        return int(row["line"]), int(row["end_line"])

    def list_symbols_by_path(self, path: str) -> List[SymbolDTO]:
        rows = self.execute(
            "SELECT * FROM symbols WHERE path = ? ORDER BY line ASC",
            (path,)
        ).fetchall()
        return [SymbolDTO.from_row(r) for r in rows]

    def search_symbols(self, query: str, limit: int = 20, **kwargs) -> List[SymbolDTO]:
        lq = f"%{query}%"
        # Join with importance_score for better ranking
        sql = "SELECT * FROM symbols WHERE (name LIKE ? OR qualname LIKE ?)"
        params = [lq, lq]
        
        if kwargs.get("kinds"):
            ks = kwargs["kinds"]
            placeholders = ",".join(["?"] * len(ks))
            sql += f" AND kind IN ({placeholders})"
            params.extend(ks)
        elif kwargs.get("kind"):
            sql += " AND kind = ?"
            params.append(kwargs["kind"])
            
        if kwargs.get("root_ids"):
            rs = kwargs["root_ids"]
            placeholders = ",".join(["?"] * len(rs))
            sql += f" AND root_id IN ({placeholders})"
            params.extend(rs)
            
        sql += " ORDER BY importance_score DESC, name ASC LIMIT ?"
        params.append(limit)
        
        rows = self.execute(sql, params).fetchall()
        return [SymbolDTO.from_row(r) for r in rows]

    def fuzzy_search_symbols(self, query: str, limit: int = 5, min_score: float = 0.6) -> List[SymbolDTO]:
        """Typo-resilient search using SequenceMatcher."""
        import difflib
        # Get candidates from DB first (broad filter)
        all_names = [r[0] for r in self.execute("SELECT name FROM symbols GROUP BY name").fetchall()]
        
        # Find close matches
        matches = difflib.get_close_matches(query, all_names, n=limit, cutoff=min_score)
        if not matches: return []
        
        placeholders = ",".join(["?"] * len(matches))
        rows = self.execute(f"SELECT * FROM symbols WHERE name IN ({placeholders}) ORDER BY importance_score DESC", matches).fetchall()
        return [SymbolDTO.from_row(r) for r in rows]

    def recalculate_symbol_importance(self) -> int:
        """Batch recalculates importance_score for all symbols based on Graph Density."""
        # 1. Reset scores
        self.execute("UPDATE symbols SET importance_score = 0.0")
        
        # 2. Degree Centrality: Simple count of incoming relations (Fan-in)
        # We also boost symbols that are called from multiple different files
        sql = """
            UPDATE symbols 
            SET importance_score = (
                SELECT COUNT(DISTINCT r.from_path) * 1.5 + COUNT(r.from_symbol) 
                FROM symbol_relations r 
                WHERE r.to_symbol_id = symbols.symbol_id 
                   OR (r.to_symbol = symbols.name AND (r.to_symbol_id IS NULL OR r.to_symbol_id = ''))
            )
        """
        self.execute(sql)
        self.conn.commit()
        
        # Return count of updated core symbols
        return self.execute("SELECT COUNT(1) FROM symbols WHERE importance_score > 0").fetchone()[0]

    def get_symbol_fan_in_stats(self, symbol_names: List[str]) -> Dict[str, int]:
        """Calculates how many times each symbol is called across the codebase."""
        if not symbol_names: return {}
        placeholders = ",".join(["?"] * len(symbol_names))
        sql = f"SELECT to_symbol, COUNT(1) FROM symbol_relations WHERE to_symbol IN ({placeholders}) GROUP BY to_symbol"
        rows = self.execute(sql, symbol_names).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_transitive_implementations(self, target_sid: str, target_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Uses a Recursive CTE to find all direct and indirect implementers/descendants."""
        sql = """
        WITH RECURSIVE inheritance_tree AS (
            -- Base case: direct implementers
            SELECT from_path, from_symbol, from_symbol_id, rel_type, line, 1 as depth
            FROM symbol_relations
            WHERE (to_symbol_id = ? OR (to_symbol = ? AND (to_symbol_id IS NULL OR to_symbol_id = '')))
              AND (rel_type = 'implements' OR rel_type = 'extends' OR rel_type = 'overrides')
            
            UNION ALL
            
            -- Recursive step: implementers of implementers
            SELECT r.from_path, r.from_symbol, r.from_symbol_id, r.rel_type, r.line, it.depth + 1
            FROM symbol_relations r
            JOIN inheritance_tree it ON r.to_symbol_id = it.from_symbol_id
            WHERE (r.rel_type = 'implements' OR r.rel_type = 'extends' OR r.rel_type = 'overrides')
              AND it.depth < 5 -- Safety limit for recursion depth
        )
        SELECT DISTINCT from_path, from_symbol, from_symbol_id, rel_type, line 
        FROM inheritance_tree 
        LIMIT ?
        """
        rows = self.execute(sql, (target_sid, target_name, limit)).fetchall()
        return [
            {
                "implementer_path": r[0],
                "implementer_symbol": r[1],
                "implementer_symbol_id": r[2],
                "rel_type": r[3],
                "line": r[4]
            } for r in rows
        ]
