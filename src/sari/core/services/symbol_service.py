import re
import logging
import sqlite3
from typing import Dict, List, Optional
from ..db.main import LocalSearchDB
from ..fallback_governance import note_fallback_event
from ..models import SymbolDTO, ImplementationHitDTO

logger = logging.getLogger("sari.symbol_service")


class SymbolService:
    def __init__(self, db: LocalSearchDB):
        self.db = db

    def search(self, query: str, limit: int = 20, **kwargs) -> List[SymbolDTO]:
        """Unified search interface for symbols."""
        if not query:
            return []
        return self.db.symbols.search_symbols(query, limit=limit, **kwargs)

    def get_implementations(self,
                            target_name: str,
                            symbol_id: str = "",
                            path: str = "",
                            limit: int = 100,
                            root_ids: Optional[List[str]] = None) -> List[Dict[str,
                                                                               object]]:
        results: List[Dict[str, object]] = []
        limit = self._normalize_limit(limit)

        # 1. Primary: Direct relations
        try:
            rows = self._query_direct_implementations(
                target_name=target_name,
                symbol_id=symbol_id,
                path=path,
                limit=limit,
            )
            results = [ImplementationHitDTO.from_row(r).model_dump() for r in rows]
        except sqlite3.Error as e:
            logger.debug(f"Direct implementation search failed: {e}")

        # 2. Secondary: Text search fallback
        if not results and target_name:
            note_fallback_event(
                "symbol_implementation_text_fallback",
                trigger="direct_relation_empty_or_failed",
                exit_condition="text_pattern_search_returned",
            )
            results = self._fallback_text_search(target_name, limit, root_ids)

        return results

    def _fallback_text_search(self,
                              target_name: str,
                              limit: int,
                              root_ids: Optional[List[str]]) -> List[Dict[str,
                                                                          object]]:
        results: List[Dict[str, object]] = []
        pattern = rf"\b(class|interface|type)\s+(\w+).*?\b(implements|extends|from)\s+{re.escape(target_name)}\b"

        # SQL using broad LIKE first
        h_sql = "SELECT path, content FROM files WHERE (content LIKE ? OR content LIKE ?)"
        h_params = [f"%implements {target_name}%", f"%extends {target_name}%"]

        if root_ids:
            h_sql += " AND (" + \
                " OR ".join(["root_id = ?"] * len(root_ids)) + ")"
            h_params.extend(root_ids)

        h_sql += " LIMIT ?"
        h_params.append(limit)

        try:
            rows = self._read_conn().execute(h_sql, h_params).fetchall()
            for r in rows:
                file_path = self._row_get(r, "path", 0, "")
                text = self._row_get(r, "content", 1, "") or ""
                if isinstance(text, bytes):
                    from ..utils.compression import _decompress
                    text = _decompress(text).decode("utf-8", errors="ignore")

                for match in re.finditer(
                        pattern, text, re.IGNORECASE | re.DOTALL):
                    symbol_name = match.group(2)
                    line = text.count("\n", 0, match.start()) + 1

                    # Try to find the actual symbol in DB
                    sym_row = self._read_conn().execute(
                        "SELECT symbol_id, name FROM symbols WHERE path = ? AND name = ? LIMIT 1",
                        (file_path, symbol_name)
                    ).fetchone()

                    results.append({
                        "implementer_path": file_path,
                        "implementer_symbol": self._row_get(sym_row, "name", 1, symbol_name) if sym_row else symbol_name,
                        "implementer_sid": self._row_get(sym_row, "symbol_id", 0, "") if sym_row else "",
                        "rel_type": match.group(3).lower(),
                        "line": line
                    })
        except sqlite3.Error as e:
            logger.debug(f"Implementation fallback search failed: {e}")

        return results

    def _query_direct_implementations(
        self,
        target_name: str,
        symbol_id: str,
        path: str,
        limit: int,
    ):
        params: List[object] = []
        if symbol_id:
            sql = "SELECT from_path, from_symbol, from_symbol_id, rel_type, line FROM symbol_relations WHERE to_symbol_id = ? AND (rel_type IN ('implements', 'extends', 'overrides'))"
            params.append(symbol_id)
        else:
            sql = "SELECT from_path, from_symbol, from_symbol_id, rel_type, line FROM symbol_relations WHERE to_symbol = ? AND (rel_type IN ('implements', 'extends', 'overrides'))"
            params.append(target_name)
            if path:
                sql += " AND (to_path = ? OR to_path IS NULL OR to_path = '')"
                params.append(path)
        sql += " ORDER BY from_path, line LIMIT ?"
        params.append(limit)
        return self._read_conn().execute(sql, params).fetchall()

    def _read_conn(self):
        if hasattr(self.db, "get_read_connection"):
            return self.db.get_read_connection()
        return self.db._read

    def _normalize_limit(self, limit: object) -> int:
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 100
        return max(1, min(n, 500))

    @staticmethod
    def _row_get(row: object, key: str, index: int, default: object = None) -> object:
        if row is None:
            return default
        try:
            if hasattr(row, "keys"):
                return row[key]
        except Exception:
            pass
        if isinstance(row, (list, tuple)) and len(row) > index:
            return row[index]
        return default
