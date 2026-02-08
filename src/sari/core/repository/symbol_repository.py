import sqlite3
from typing import Iterable, List, Optional, Tuple

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
            INSERT INTO symbols(symbol_id, path, root_id, name, kind, line, end_line, content, parent_name, metadata, docstring, qualname)
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
            INSERT INTO symbol_relations(from_path, from_root_id, from_symbol, from_symbol_id, to_path, to_root_id, to_symbol, to_symbol_id, rel_type, line)
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
