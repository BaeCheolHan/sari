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
        scope_repo_root: str | None = None,
        relative_path: str,
        content_hash: str,
        symbols: list[dict[str, object]],
        degraded: bool,
        l3_skipped_large_file: bool,
        updated_at: str,
    ) -> None:
        self.upsert_l3_symbols_many(
            [
                {
                    "workspace_id": workspace_id,
                    "repo_root": repo_root,
                    "scope_repo_root": scope_repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                    "symbols": symbols,
                    "degraded": degraded,
                    "l3_skipped_large_file": l3_skipped_large_file,
                    "updated_at": updated_at,
                }
            ]
        )

    def upsert_l3_symbols_many(self, upserts: list[dict[str, object]], *, conn=None) -> None:
        if len(upserts) == 0:
            return
        params = [
            {
                "workspace_id": str(item["workspace_id"]),
                "repo_root": str(item["repo_root"]),
                "scope_repo_root": str(item.get("scope_repo_root") or item["repo_root"]),
                "relative_path": str(item["relative_path"]),
                "content_hash": str(item["content_hash"]),
                "symbols_json": json.dumps(item.get("symbols", []), ensure_ascii=False),
                "degraded": 1 if bool(item.get("degraded", False)) else 0,
                "l3_skipped_large_file": 1 if bool(item.get("l3_skipped_large_file", False)) else 0,
                "updated_at": str(item["updated_at"]),
            }
            for item in upserts
        ]
        owned_conn = conn is None
        if owned_conn:
            conn = connect(self._db_path)
        if conn is None:
            raise RuntimeError("conn must not be None when owned_conn is False")
        try:
            conn.executemany(
                """
                INSERT INTO tool_data_l3_symbols(
                    workspace_id, repo_root, scope_repo_root, relative_path, content_hash, symbols_json,
                    degraded, l3_skipped_large_file, updated_at
                )
                VALUES(
                    :workspace_id, :repo_root, :scope_repo_root, :relative_path, :content_hash, :symbols_json,
                    :degraded, :l3_skipped_large_file, :updated_at
                )
                ON CONFLICT(workspace_id, repo_root, relative_path, content_hash) DO UPDATE SET
                    scope_repo_root = excluded.scope_repo_root,
                    symbols_json = excluded.symbols_json,
                    degraded = excluded.degraded,
                    l3_skipped_large_file = excluded.l3_skipped_large_file,
                    updated_at = excluded.updated_at
                """,
                params,
            )
            if owned_conn:
                conn.commit()
        finally:
            if owned_conn:
                conn.close()

    def upsert_l4_normalized_symbols(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        scope_repo_root: str | None = None,
        relative_path: str,
        content_hash: str,
        normalized: dict[str, object],
        confidence: float,
        ambiguity: float,
        coverage: float,
        updated_at: str,
    ) -> None:
        self.upsert_l4_normalized_symbols_many(
            [
                {
                    "workspace_id": workspace_id,
                    "repo_root": repo_root,
                    "scope_repo_root": scope_repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                    "normalized": normalized,
                    "confidence": confidence,
                    "ambiguity": ambiguity,
                    "coverage": coverage,
                    "updated_at": updated_at,
                }
            ]
        )

    def upsert_l4_normalized_symbols_many(self, upserts: list[dict[str, object]], *, conn=None) -> None:
        if len(upserts) == 0:
            return
        params = [
            {
                "workspace_id": str(item["workspace_id"]),
                "repo_root": str(item["repo_root"]),
                "scope_repo_root": str(item.get("scope_repo_root") or item["repo_root"]),
                "relative_path": str(item["relative_path"]),
                "content_hash": str(item["content_hash"]),
                "normalized_json": json.dumps(item.get("normalized", {}), ensure_ascii=False),
                "confidence": float(item.get("confidence", 0.0)),
                "ambiguity": float(item.get("ambiguity", 0.0)),
                "coverage": float(item.get("coverage", 0.0)),
                "updated_at": str(item["updated_at"]),
            }
            for item in upserts
        ]
        owned_conn = conn is None
        if owned_conn:
            conn = connect(self._db_path)
        if conn is None:
            raise RuntimeError("conn must not be None when owned_conn is False")
        try:
            conn.executemany(
                """
                INSERT INTO tool_data_l4_normalized_symbols(
                    workspace_id, repo_root, scope_repo_root, relative_path, content_hash, normalized_json,
                    confidence, ambiguity, coverage, updated_at
                )
                VALUES(
                    :workspace_id, :repo_root, :scope_repo_root, :relative_path, :content_hash, :normalized_json,
                    :confidence, :ambiguity, :coverage, :updated_at
                )
                ON CONFLICT(workspace_id, repo_root, relative_path, content_hash) DO UPDATE SET
                    scope_repo_root = excluded.scope_repo_root,
                    normalized_json = excluded.normalized_json,
                    confidence = excluded.confidence,
                    ambiguity = excluded.ambiguity,
                    coverage = excluded.coverage,
                    updated_at = excluded.updated_at
                """,
                params,
            )
            if owned_conn:
                conn.commit()
        finally:
            if owned_conn:
                conn.close()

    def upsert_l5_semantics(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        scope_repo_root: str | None = None,
        relative_path: str,
        content_hash: str,
        reason_code: str,
        semantics: dict[str, object],
        updated_at: str,
    ) -> None:
        self.upsert_l5_semantics_many(
            [
                {
                    "workspace_id": workspace_id,
                    "repo_root": repo_root,
                    "scope_repo_root": scope_repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                    "reason_code": reason_code,
                    "semantics": semantics,
                    "updated_at": updated_at,
                }
            ]
        )

    def upsert_l5_semantics_many(self, upserts: list[dict[str, object]], *, conn=None) -> None:
        if len(upserts) == 0:
            return
        params = [
            {
                "workspace_id": str(item["workspace_id"]),
                "repo_root": str(item["repo_root"]),
                "scope_repo_root": str(item.get("scope_repo_root") or item["repo_root"]),
                "relative_path": str(item["relative_path"]),
                "content_hash": str(item["content_hash"]),
                "reason_code": str(item["reason_code"]),
                "semantics_json": json.dumps(item.get("semantics", {}), ensure_ascii=False),
                "updated_at": str(item["updated_at"]),
            }
            for item in upserts
        ]
        owned_conn = conn is None
        if owned_conn:
            conn = connect(self._db_path)
        if conn is None:
            raise RuntimeError("conn must not be None when owned_conn is False")
        try:
            conn.executemany(
                """
                INSERT INTO tool_data_l5_semantics(
                    workspace_id, repo_root, scope_repo_root, relative_path, content_hash, reason_code, semantics_json, updated_at
                )
                SELECT
                    :workspace_id, :repo_root, :scope_repo_root, :relative_path, :content_hash, :reason_code, :semantics_json, :updated_at
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
                    scope_repo_root = excluded.scope_repo_root,
                    semantics_json = excluded.semantics_json,
                    updated_at = excluded.updated_at
                """,
                params,
            )
            if owned_conn:
                conn.commit()
        finally:
            if owned_conn:
                conn.close()

    def load_effective_snapshot(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        relative_path: str,
        content_hash: str,
    ) -> dict[str, object]:
        """content_hash 일치 레코드만 로드한다."""
        with connect(self._db_path) as conn:
            resolved_repo_root = self._resolve_effective_repo_root(
                conn=conn,
                repo_root=repo_root,
                relative_path=relative_path,
                content_hash=content_hash,
            )
            if resolved_repo_root is None:
                return {"l3": None, "l4": None, "l5": []}
            workspace_ids = self._workspace_id_candidates_for_effective(
                workspace_id=workspace_id,
                requested_repo_root=repo_root,
                effective_repo_root=resolved_repo_root,
            )
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
                    "repo_root": resolved_repo_root,
                    "relative_path": relative_path,
                },
            ).fetchone()
            if active_row is None or str(active_row["content_hash"]) != content_hash:
                return {"l3": None, "l4": None, "l5": []}
            l3_row = conn.execute(
                """
                SELECT symbols_json, degraded, l3_skipped_large_file, updated_at
                FROM tool_data_l3_symbols
                WHERE workspace_id IN (
                    :workspace_id_1, :workspace_id_2, :workspace_id_3, :workspace_id_4, :workspace_id_5
                )
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                """,
                {
                    "workspace_id_1": workspace_ids[0],
                    "workspace_id_2": workspace_ids[1],
                    "workspace_id_3": workspace_ids[2],
                    "workspace_id_4": workspace_ids[3],
                    "workspace_id_5": workspace_ids[4],
                    "repo_root": resolved_repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchone()
            l4_row = conn.execute(
                """
                SELECT normalized_json, confidence, ambiguity, coverage, updated_at
                FROM tool_data_l4_normalized_symbols
                WHERE workspace_id IN (
                    :workspace_id_1, :workspace_id_2, :workspace_id_3, :workspace_id_4, :workspace_id_5
                )
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                """,
                {
                    "workspace_id_1": workspace_ids[0],
                    "workspace_id_2": workspace_ids[1],
                    "workspace_id_3": workspace_ids[2],
                    "workspace_id_4": workspace_ids[3],
                    "workspace_id_5": workspace_ids[4],
                    "repo_root": resolved_repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchone()
            l5_rows = conn.execute(
                """
                SELECT reason_code, semantics_json, updated_at
                FROM tool_data_l5_semantics
                WHERE workspace_id IN (
                    :workspace_id_1, :workspace_id_2, :workspace_id_3, :workspace_id_4, :workspace_id_5
                )
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                ORDER BY reason_code
                """,
                {
                    "workspace_id_1": workspace_ids[0],
                    "workspace_id_2": workspace_ids[1],
                    "workspace_id_3": workspace_ids[2],
                    "workspace_id_4": workspace_ids[3],
                    "workspace_id_5": workspace_ids[4],
                    "repo_root": resolved_repo_root,
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

    def has_l5_semantics(
        self,
        *,
        repo_root: str,
        relative_path: str,
        content_hash: str,
    ) -> bool:
        """해당 파일에 L5 semantics가 이미 저장되어 있는지 확인한다."""
        ws1, ws2 = self._workspace_id_candidates(workspace_id=repo_root, repo_root=repo_root)
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM tool_data_l5_semantics
                WHERE workspace_id IN (:ws1, :ws2)
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                LIMIT 1
                """,
                {
                    "ws1": ws1,
                    "ws2": ws2,
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchone()
        return row is not None

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
            "scope_repo_root": repo_root,
        }
        where_prefix = ""
        if path_prefix is not None and path_prefix.strip() != "":
            where_prefix = "AND l3.relative_path LIKE :path_prefix"
            params["path_prefix"] = f"{path_prefix.strip()}%"
        with connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT l3.repo_root, l3.relative_path, l3.content_hash, l3.symbols_json, l3.updated_at
                FROM tool_data_l3_symbols AS l3
                JOIN collected_files_l1 AS f
                  ON f.repo_root = l3.repo_root
                 AND f.relative_path = l3.relative_path
                 AND f.content_hash = l3.content_hash
                 AND f.is_deleted = 0
                WHERE (l3.workspace_id = :workspace_id OR l3.workspace_id = :workspace_id_legacy)
                  AND (f.scope_repo_root = :scope_repo_root OR f.repo_root = :scope_repo_root)
                  {where_prefix}
                ORDER BY l3.updated_at DESC, l3.repo_root ASC, l3.relative_path ASC
                LIMIT 500
                """,
                params,
            ).fetchall()
            if len(rows) == 0:
                # 일부 writer가 module-root workspace_id로 저장한 혼합 구간을 위해 scope/file 기준으로 폴백한다.
                # scope 조회에서 workspace_id 불일치만으로 L3/L4/L5가 누락되지 않아야 한다.
                rows = conn.execute(
                    f"""
                    SELECT l3.repo_root, l3.relative_path, l3.content_hash, l3.symbols_json, l3.updated_at
                    FROM tool_data_l3_symbols AS l3
                    JOIN collected_files_l1 AS f
                      ON f.repo_root = l3.repo_root
                     AND f.relative_path = l3.relative_path
                     AND f.content_hash = l3.content_hash
                     AND f.is_deleted = 0
                    WHERE (f.scope_repo_root = :scope_repo_root OR f.repo_root = :scope_repo_root)
                      {where_prefix}
                    ORDER BY l3.updated_at DESC, l3.repo_root ASC, l3.relative_path ASC
                    LIMIT 500
                    """,
                    params,
                ).fetchall()
        needle = query.lower()
        results: list[dict[str, object]] = []
        for row in rows:
            effective_repo_root = str(row["repo_root"])
            relative_path = str(row["relative_path"])
            content_hash = str(row["content_hash"])
            raw_symbols = json.loads(str(row["symbols_json"]))
            if not isinstance(raw_symbols, list):
                continue
            snapshot = self.load_effective_snapshot(
                workspace_id=workspace_id,
                repo_root=effective_repo_root,
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
                        "repo": effective_repo_root,
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

    def _resolve_effective_repo_root(
        self,
        *,
        conn,
        repo_root: str,
        relative_path: str,
        content_hash: str,
    ) -> str | None:
        rows = conn.execute(
            """
            SELECT repo_root, scope_repo_root, content_hash
            FROM collected_files_l1
            WHERE relative_path = :relative_path
              AND is_deleted = 0
              AND (repo_root = :repo_root OR scope_repo_root = :scope_repo_root)
            ORDER BY CASE WHEN repo_root = :repo_root THEN 0 ELSE 1 END, updated_at DESC
            """,
            {
                "repo_root": repo_root,
                "scope_repo_root": repo_root,
                "relative_path": relative_path,
            },
        ).fetchall()
        if len(rows) == 0:
            return None

        direct = [row for row in rows if str(row["repo_root"]) == repo_root]
        for row in direct:
            if str(row["content_hash"]) == content_hash:
                return str(row["repo_root"])

        scoped = [row for row in rows if str(row["scope_repo_root"]) == repo_root and str(row["content_hash"]) == content_hash]
        scoped_roots = {str(row["repo_root"]) for row in scoped}
        if len(scoped_roots) == 1:
            return next(iter(scoped_roots))
        return None

    def resolve_effective_repo_root(
        self,
        *,
        repo_root: str,
        relative_path: str,
        content_hash: str,
    ) -> str | None:
        """repo/scope 혼합 저장 상태에서 effective repo_root를 계산해 반환한다."""
        with connect(self._db_path) as conn:
            return self._resolve_effective_repo_root(
                conn=conn,
                repo_root=repo_root,
                relative_path=relative_path,
                content_hash=content_hash,
            )

    def _workspace_id_candidates(self, *, workspace_id: str, repo_root: str) -> tuple[str, str]:
        primary = str(workspace_id or "").strip()
        if primary == "":
            primary = str(repo_root or "").strip()
        legacy = hashlib.sha1(str(repo_root or "").strip().encode("utf-8")).hexdigest()
        if primary == legacy:
            return (primary, primary)
        return (primary, legacy)

    def _workspace_id_candidates_for_effective(
        self,
        *,
        workspace_id: str,
        requested_repo_root: str,
        effective_repo_root: str,
    ) -> tuple[str, str, str, str, str]:
        values: list[str] = []
        for candidate in (
            str(workspace_id or "").strip(),
            str(requested_repo_root or "").strip(),
            str(effective_repo_root or "").strip(),
        ):
            if candidate != "" and candidate not in values:
                values.append(candidate)
        for candidate in (requested_repo_root, effective_repo_root):
            normalized = str(candidate or "").strip()
            if normalized == "":
                continue
            digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
            if digest not in values:
                values.append(digest)
        while len(values) < 5:
            values.append("__unused_workspace_id__")
        return (values[0], values[1], values[2], values[3], values[4])

    def list_l5_upgrade_candidates(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        """현재 버전의 L5 semantics가 없는 파일 목록을 반환한다.

        content_hash 3-way JOIN으로 현재 활성 버전만 조회한다.
        confidence ASC 정렬(낮은 신뢰도 우선)로 L5 처리 순서를 결정한다.

        Returns:
            list of dicts with keys: repo_root, relative_path, content_hash, confidence
        """
        ws1, ws2 = self._workspace_id_candidates(workspace_id=workspace_id, repo_root=repo_root)
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    f.repo_root,
                    f.relative_path,
                    f.content_hash,
                    q.confidence
                FROM collected_files_l1 f
                JOIN tool_data_l4_normalized_symbols q
                    ON  f.repo_root     = q.repo_root
                    AND f.relative_path = q.relative_path
                    AND f.content_hash  = q.content_hash
                LEFT JOIN tool_data_l5_semantics s
                    ON  q.workspace_id  = s.workspace_id
                    AND f.repo_root     = s.repo_root
                    AND f.relative_path = s.relative_path
                    AND f.content_hash  = s.content_hash
                WHERE f.repo_root = :repo_root
                  AND f.is_deleted = 0
                  AND q.workspace_id IN (:ws1, :ws2)
                  AND s.workspace_id IS NULL
                ORDER BY q.confidence ASC
                LIMIT :limit
                """,
                {
                    "repo_root": repo_root,
                    "ws1": ws1,
                    "ws2": ws2,
                    "limit": max(1, int(limit)),
                },
            ).fetchall()
        return [dict(row) for row in rows]

    def count_l5_stale(
        self,
        *,
        workspace_id: str,
        repo_root: str,
    ) -> int:
        """현재 버전의 L5 semantics가 없는 파일 수를 반환한다.

        trigger_startup()에서 daemon 재기동 시 stale 파일 유무 확인에 사용.
        """
        ws1, ws2 = self._workspace_id_candidates(workspace_id=workspace_id, repo_root=repo_root)
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM collected_files_l1 f
                JOIN tool_data_l4_normalized_symbols q
                    ON  f.repo_root     = q.repo_root
                    AND f.relative_path = q.relative_path
                    AND f.content_hash  = q.content_hash
                LEFT JOIN tool_data_l5_semantics s
                    ON  q.workspace_id  = s.workspace_id
                    AND f.repo_root     = s.repo_root
                    AND f.relative_path = s.relative_path
                    AND f.content_hash  = s.content_hash
                WHERE f.repo_root = :repo_root
                  AND f.is_deleted = 0
                  AND q.workspace_id IN (:ws1, :ws2)
                  AND s.workspace_id IS NULL
                """,
                {
                    "repo_root": repo_root,
                    "ws1": ws1,
                    "ws2": ws2,
                },
            ).fetchone()
        return int(row["cnt"]) if row else 0
