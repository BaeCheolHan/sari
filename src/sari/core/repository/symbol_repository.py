import sqlite3
import json
from typing import Iterable, List, Set
from .base import BaseRepository
from ..parsers.common import _symbol_id

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
            while len(vals) < 7:
                vals.append("")
            if len(vals) < 8:
                vals.append("{}")
            if len(vals) < 9:
                vals.append("")
                
            path = str(vals[0]) if vals else ""
            name = str(vals[1]) if len(vals) > 1 else ""
            kind = str(vals[2]) if len(vals) > 2 else ""
            parent = str(vals[6]) if len(vals) > 6 else ""
            
            if len(vals) <= 9:
                qualname = f"{parent}.{name}" if parent else name
                vals.append(qualname)
            else:
                qualname = str(vals[9]) if vals[9] else (f"{parent}.{name}" if parent else name)
                vals[9] = qualname
                
            if len(vals) <= 10:
                vals.append(_symbol_id(path, kind, qualname))
            else:
                if not vals[10]:
                    vals[10] = _symbol_id(path, kind, qualname)
            normalized.append(tuple(vals[:11]))
            
        symbols_list = normalized
        paths = {s[0] for s in symbols_list}
        cur.executemany("DELETE FROM symbols WHERE path = ?", [(p,) for p in paths])
        cur.executemany(
            """
            INSERT INTO symbols(path, name, kind, line, end_line, content, parent_name, metadata, docstring, qualname, symbol_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            symbols_list,
        )
        return len(symbols_list)

    def upsert_relations_tx(self, cur: sqlite3.Cursor, relations: Iterable[tuple]) -> int:
        rels_list = list(relations)
        if not rels_list:
            return 0
            
        normalized = []
        for r in rels_list:
            vals = list(r)
            if len(vals) == 6:
                from_path, from_symbol, to_path, to_symbol, rel_type, line = vals
                normalized.append((from_path, from_symbol, "", to_path, to_symbol, "", rel_type, line))
                continue
            if len(vals) < 8:
                vals = vals + [""] * (8 - len(vals))
                
            if len(vals) >= 8 and isinstance(vals[5], int) and isinstance(vals[4], str) and vals[4] in {"calls", "extends", "implements"}:
                from_path, from_symbol, from_sid, to_path, to_symbol, to_sid, rel_type, line = vals[:8]
                normalized.append((from_path, from_symbol, from_sid, to_path, to_symbol, to_sid, rel_type, line))
            else:
                normalized.append(tuple(vals[:8]))
                
        rels_list = normalized
        paths = {r[0] for r in rels_list}
        cur.executemany("DELETE FROM symbol_relations WHERE from_path = ?", [(p,) for p in paths])
        cur.executemany(
            """
            INSERT INTO symbol_relations(from_path, from_symbol, from_symbol_id, to_path, to_symbol, to_symbol_id, rel_type, line)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            rels_list,
        )
        return len(rels_list)
