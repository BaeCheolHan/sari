import re
import logging
from typing import List, Dict, Any, Optional
from ..db.main import LocalSearchDB
from ..models import SymbolDTO

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
                                                                               Any]]:
        results = []

        # 1. Primary: Direct relations
        try:
            params = []
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

            rows = self.db._read.execute(sql, params).fetchall()
            for r in rows:
                results.append({
                    "implementer_path": r[0], "implementer_symbol": r[1],
                    "implementer_sid": r[2] or "", "rel_type": r[3], "line": r[4]
                })
        except Exception as e:
            logger.debug(f"Direct implementation search failed: {e}")

        # 2. Secondary: Text search fallback
        if not results and target_name:
            results = self._fallback_text_search(target_name, limit, root_ids)

        return results

    def _fallback_text_search(self,
                              target_name: str,
                              limit: int,
                              root_ids: Optional[List[str]]) -> List[Dict[str,
                                                                          Any]]:
        results = []
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
            rows = self.db._read.execute(h_sql, h_params).fetchall()
            for r in rows:
                file_path, text = r[0], r[1] or ""
                if isinstance(text, bytes):
                    from ..utils.compression import _decompress
                    text = _decompress(text).decode("utf-8", errors="ignore")

                for match in re.finditer(
                        pattern, text, re.IGNORECASE | re.DOTALL):
                    symbol_name = match.group(2)
                    line = text.count("\n", 0, match.start()) + 1

                    # Try to find the actual symbol in DB
                    sym_row = self.db._read.execute(
                        "SELECT symbol_id, name FROM symbols WHERE path = ? AND name = ? LIMIT 1",
                        (file_path, symbol_name)
                    ).fetchone()

                    results.append({
                        "implementer_path": file_path,
                        "implementer_symbol": sym_row[1] if sym_row else symbol_name,
                        "implementer_sid": sym_row[0] if sym_row else "",
                        "rel_type": match.group(3).lower(),
                        "line": line
                    })
        except Exception as e:
            logger.debug(f"Implementation fallback search failed: {e}")

        return results
