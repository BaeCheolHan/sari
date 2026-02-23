"""L3/L4/L5 분리 tool_data 저장소를 구현한다."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sari.db.schema import connect


class ToolDataLayerRepository:
    """레이어별 tool_data(L3/L4/L5) 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def upsert_l3_symbols(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        symbols: list[dict[str, object]],
        degraded: bool,
        l3_skipped_large_file: bool,
        updated_at: str,
    ) -> None:
        payload = json.dumps(symbols, ensure_ascii=False)
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO tool_data_l3_symbols(
                    workspace_id, repo_root, relative_path, content_hash, symbols_json,
                    degraded, l3_skipped_large_file, updated_at
                )
                VALUES(
                    :workspace_id, :repo_root, :relative_path, :content_hash, :symbols_json,
                    :degraded, :l3_skipped_large_file, :updated_at
                )
                ON CONFLICT(workspace_id, repo_root, relative_path, content_hash) DO UPDATE SET
                    symbols_json = excluded.symbols_json,
                    degraded = excluded.degraded,
                    l3_skipped_large_file = excluded.l3_skipped_large_file,
                    updated_at = excluded.updated_at
                """,
                {
                    "workspace_id": workspace_id,
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                    "symbols_json": payload,
                    "degraded": 1 if degraded else 0,
                    "l3_skipped_large_file": 1 if l3_skipped_large_file else 0,
                    "updated_at": updated_at,
                },
            )
            conn.commit()

    def upsert_l4_normalized_symbols(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        normalized: dict[str, object],
        confidence: float,
        ambiguity: float,
        coverage: float,
        needs_l5: bool,
        updated_at: str,
    ) -> None:
        payload = json.dumps(normalized, ensure_ascii=False)
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO tool_data_l4_normalized_symbols(
                    workspace_id, repo_root, relative_path, content_hash, normalized_json,
                    confidence, ambiguity, coverage, needs_l5, updated_at
                )
                VALUES(
                    :workspace_id, :repo_root, :relative_path, :content_hash, :normalized_json,
                    :confidence, :ambiguity, :coverage, :needs_l5, :updated_at
                )
                ON CONFLICT(workspace_id, repo_root, relative_path, content_hash) DO UPDATE SET
                    normalized_json = excluded.normalized_json,
                    confidence = excluded.confidence,
                    ambiguity = excluded.ambiguity,
                    coverage = excluded.coverage,
                    needs_l5 = excluded.needs_l5,
                    updated_at = excluded.updated_at
                """,
                {
                    "workspace_id": workspace_id,
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                    "normalized_json": payload,
                    "confidence": float(confidence),
                    "ambiguity": float(ambiguity),
                    "coverage": float(coverage),
                    "needs_l5": 1 if needs_l5 else 0,
                    "updated_at": updated_at,
                },
            )
            conn.commit()

    def upsert_l5_semantics(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        reason_code: str,
        semantics: dict[str, object],
        updated_at: str,
    ) -> None:
        payload = json.dumps(semantics, ensure_ascii=False)
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO tool_data_l5_semantics(
                    workspace_id, repo_root, relative_path, content_hash, reason_code, semantics_json, updated_at
                )
                SELECT
                    :workspace_id, :repo_root, :relative_path, :content_hash, :reason_code, :semantics_json, :updated_at
                WHERE EXISTS (
                    SELECT 1
                    FROM collected_files_l1 AS f
                    WHERE f.repo_root = :repo_root
                      AND f.relative_path = :relative_path
                      AND f.is_deleted = 0
                      AND f.content_hash = :content_hash
                )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM collected_files_l1 AS f
                    WHERE f.repo_root = :repo_root
                      AND f.relative_path = :relative_path
                      AND f.is_deleted = 0
                      AND f.content_hash <> :content_hash
                )
                ON CONFLICT(workspace_id, repo_root, relative_path, content_hash, reason_code) DO UPDATE SET
                    semantics_json = excluded.semantics_json,
                    updated_at = excluded.updated_at
                """,
                {
                    "workspace_id": workspace_id,
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                    "reason_code": reason_code,
                    "semantics_json": payload,
                    "updated_at": updated_at,
                },
            )
            conn.commit()

    def load_effective_snapshot(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        relative_path: str,
        content_hash: str,
    ) -> dict[str, object]:
        """content_hash 일치 레코드만 로드한다."""
        workspace_ids = self._workspace_id_candidates(workspace_id=workspace_id, repo_root=repo_root)
        with connect(self._db_path) as conn:
            active_row = conn.execute(
                """
                SELECT content_hash
                FROM collected_files_l1
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND is_deleted = 0
                LIMIT 1
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                },
            ).fetchone()
            if active_row is None:
                return {"l3": None, "l4": None, "l5": []}
            if str(active_row["content_hash"]) != content_hash:
                return {"l3": None, "l4": None, "l5": []}
            l3_row = conn.execute(
                """
                SELECT symbols_json, degraded, l3_skipped_large_file, updated_at
                FROM tool_data_l3_symbols
                WHERE workspace_id IN (:workspace_id, :workspace_id_legacy)
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                """,
                {
                    "workspace_id": workspace_ids[0],
                    "workspace_id_legacy": workspace_ids[1],
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchone()
            l4_row = conn.execute(
                """
                SELECT normalized_json, confidence, ambiguity, coverage, needs_l5, updated_at
                FROM tool_data_l4_normalized_symbols
                WHERE workspace_id IN (:workspace_id, :workspace_id_legacy)
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                """,
                {
                    "workspace_id": workspace_ids[0],
                    "workspace_id_legacy": workspace_ids[1],
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchone()
            l5_rows = conn.execute(
                """
                SELECT reason_code, semantics_json, updated_at
                FROM tool_data_l5_semantics
                WHERE workspace_id IN (:workspace_id, :workspace_id_legacy)
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                ORDER BY reason_code
                """,
                {
                    "workspace_id": workspace_ids[0],
                    "workspace_id_legacy": workspace_ids[1],
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchall()
        return {
            "l3": None
            if l3_row is None
            else {
                "symbols": json.loads(str(l3_row["symbols_json"])),
                "degraded": bool(int(l3_row["degraded"])),
                "l3_skipped_large_file": bool(int(l3_row["l3_skipped_large_file"])),
                "updated_at": str(l3_row["updated_at"]),
            },
            "l4": None
            if l4_row is None
            else {
                "normalized": json.loads(str(l4_row["normalized_json"])),
                "confidence": float(l4_row["confidence"]),
                "ambiguity": float(l4_row["ambiguity"]),
                "coverage": float(l4_row["coverage"]),
                "needs_l5": bool(int(l4_row["needs_l5"])),
                "updated_at": str(l4_row["updated_at"]),
            },
            "l5": [
                {
                    "reason_code": str(row["reason_code"]),
                    "semantics": json.loads(str(row["semantics_json"])),
                    "updated_at": str(row["updated_at"]),
                }
                for row in l5_rows
            ],
        }

    def drop_stale_l5_semantics(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        relative_path: str,
        active_content_hash: str,
    ) -> int:
        """활성 content_hash와 불일치하는 L5 레코드를 삭제한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                DELETE FROM tool_data_l5_semantics
                WHERE workspace_id = :workspace_id
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash <> :active_content_hash
                """,
                {
                    "workspace_id": workspace_id,
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "active_content_hash": active_content_hash,
                },
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def search_l3_symbols(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        query: str,
        limit: int,
        path_prefix: str | None = None,
    ) -> list[dict[str, object]]:
        """L3 symbols_json에서 이름 기준 심볼 후보를 검색한다."""
        workspace_ids = self._workspace_id_candidates(workspace_id=workspace_id, repo_root=repo_root)
        params: dict[str, object] = {
            "workspace_id": workspace_ids[0],
            "workspace_id_legacy": workspace_ids[1],
            "repo_root": repo_root,
        }
        where_prefix = ""
        if path_prefix is not None and path_prefix.strip() != "":
            where_prefix = "AND l3.relative_path LIKE :path_prefix"
            params["path_prefix"] = f"{path_prefix.strip()}%"
        with connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT l3.relative_path, l3.content_hash, l3.symbols_json, l3.updated_at
                FROM tool_data_l3_symbols AS l3
                JOIN collected_files_l1 AS f
                  ON f.repo_root = l3.repo_root
                 AND f.relative_path = l3.relative_path
                 AND f.content_hash = l3.content_hash
                 AND f.is_deleted = 0
                WHERE (l3.workspace_id = :workspace_id OR l3.workspace_id = :workspace_id_legacy)
                  AND l3.repo_root = :repo_root
                  {where_prefix}
                ORDER BY l3.updated_at DESC, l3.relative_path ASC
                LIMIT 500
                """,
                params,
            ).fetchall()
        needle = query.lower()
        results: list[dict[str, object]] = []
        for row in rows:
            relative_path = str(row["relative_path"])
            content_hash = str(row["content_hash"])
            raw_symbols = json.loads(str(row["symbols_json"]))
            if not isinstance(raw_symbols, list):
                continue
            snapshot = self.load_effective_snapshot(
                workspace_id=workspace_id,
                repo_root=repo_root,
                relative_path=relative_path,
                content_hash=content_hash,
            )
            for symbol in raw_symbols:
                if not isinstance(symbol, dict):
                    continue
                name = str(symbol.get("name", ""))
                if needle not in name.lower():
                    continue
                results.append(
                    {
                        "repo": repo_root,
                        "relative_path": relative_path,
                        "name": name,
                        "kind": str(symbol.get("kind", "")),
                        "line": int(symbol.get("line", 0)),
                        "end_line": int(symbol.get("end_line", 0)),
                        "content_hash": content_hash,
                        "symbol_key": symbol.get("symbol_key"),
                        "parent_symbol_key": symbol.get("parent_symbol_key"),
                        "depth": int(symbol.get("depth", 0)),
                        "container_name": symbol.get("container_name"),
                        "l4": snapshot.get("l4"),
                        "l5": snapshot.get("l5", []),
                    }
                )
                if len(results) >= limit:
                    return results
        return results

    def _workspace_id_candidates(self, *, workspace_id: str, repo_root: str) -> tuple[str, str]:
        primary = str(workspace_id or "").strip()
        if primary == "":
            primary = str(repo_root or "").strip()
        legacy = hashlib.sha1(str(repo_root or "").strip().encode("utf-8")).hexdigest()
        if primary == legacy:
            return (primary, primary)
        return (primary, legacy)
