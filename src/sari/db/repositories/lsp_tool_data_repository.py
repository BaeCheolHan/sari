"""LSP 구조 데이터 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CallerEdgeDTO, LspExtractPersistDTO, SymbolSearchItemDTO
from sari.db.row_mapper import row_int, row_str
from sari.db.schema import connect


def _optional_str(value: object) -> str | None:
    """옵셔널 문자열 값을 안전하게 정규화한다."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped != "":
            return stripped
    return None


def _canonical_file_key(*, repo_root: str, relative_path: str) -> str:
    """repo_root/relative_path 조합을 절대 파일 키로 정규화한다."""
    base = Path(repo_root.strip()) if repo_root.strip() != "" else Path("/")
    try:
        return str((base / relative_path).resolve(strict=False))
    except OSError:
        return str((base / relative_path).absolute())


class LspToolDataRepository:
    """LSP 기반 심볼/관계 데이터 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def replace_symbols(
        self,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        symbols: list[dict[str, object]],
        created_at: str,
    ) -> None:
        """파일 단위 심볼 데이터를 교체 저장한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                DELETE FROM lsp_symbols
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                """,
                {"repo_root": repo_root, "relative_path": relative_path},
            )
            for symbol in self._dedupe_symbols(symbols):
                conn.execute(
                    """
                    INSERT INTO lsp_symbols(
                        repo_root, relative_path, content_hash, name, kind, line, end_line,
                        symbol_key, parent_symbol_key, depth, container_name, created_at
                    )
                    VALUES(
                        :repo_root, :relative_path, :content_hash, :name, :kind, :line, :end_line,
                        :symbol_key, :parent_symbol_key, :depth, :container_name, :created_at
                    )
                    """,
                    {
                        "repo_root": repo_root,
                        "relative_path": relative_path,
                        "content_hash": content_hash,
                        "name": str(symbol.get("name", "")),
                        "kind": str(symbol.get("kind", "")),
                        "line": int(symbol.get("line", 0)),
                        "end_line": int(symbol.get("end_line", 0)),
                        "symbol_key": _optional_str(symbol.get("symbol_key")),
                        "parent_symbol_key": _optional_str(symbol.get("parent_symbol_key")),
                        "depth": int(symbol.get("depth", 0)),
                        "container_name": _optional_str(symbol.get("container_name")),
                        "created_at": created_at,
                    },
                )
            conn.commit()

    def replace_relations(
        self,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        relations: list[dict[str, object]],
        created_at: str,
    ) -> None:
        """파일 단위 호출 관계 데이터를 교체 저장한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                DELETE FROM lsp_call_relations
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                """,
                {"repo_root": repo_root, "relative_path": relative_path},
            )
            for relation in self._dedupe_relations(relations):
                conn.execute(
                    """
                    INSERT INTO lsp_call_relations(
                        repo_root, relative_path, content_hash, from_symbol, to_symbol, line, created_at
                    )
                    VALUES(
                        :repo_root, :relative_path, :content_hash, :from_symbol, :to_symbol, :line, :created_at
                    )
                    """,
                    {
                        "repo_root": repo_root,
                        "relative_path": relative_path,
                        "content_hash": content_hash,
                        "from_symbol": str(relation.get("from_symbol", "")),
                        "to_symbol": str(relation.get("to_symbol", "")),
                        "line": int(relation.get("line", 0)),
                        "created_at": created_at,
                    },
            )
            conn.commit()

    def replace_file_data_many(self, items: list[LspExtractPersistDTO], *, conn=None) -> None:
        """파일 단위 심볼/관계 데이터를 배치 교체 저장한다."""
        if len(items) == 0:
            return
        owned_conn = conn is None
        if owned_conn:
            conn = connect(self._db_path)
        if conn is None:
            raise RuntimeError("conn must not be None when owned_conn is False")
        try:
            for item in items:
                conn.execute(
                    """
                    DELETE FROM lsp_symbols
                    WHERE repo_root = :repo_root
                      AND relative_path = :relative_path
                    """,
                    {"repo_root": item.repo_root, "relative_path": item.relative_path},
                )
                symbol_rows: list[dict[str, object]] = []
                for symbol in self._dedupe_symbols(item.symbols):
                    symbol_rows.append(
                        {
                            "repo_root": item.repo_root,
                            "relative_path": item.relative_path,
                            "content_hash": item.content_hash,
                            "name": str(symbol.get("name", "")),
                            "kind": str(symbol.get("kind", "")),
                            "line": int(symbol.get("line", 0)),
                            "end_line": int(symbol.get("end_line", 0)),
                            "symbol_key": _optional_str(symbol.get("symbol_key")),
                            "parent_symbol_key": _optional_str(symbol.get("parent_symbol_key")),
                            "depth": int(symbol.get("depth", 0)),
                            "container_name": _optional_str(symbol.get("container_name")),
                            "created_at": item.created_at,
                        }
                    )
                if len(symbol_rows) > 0:
                    conn.executemany(
                        """
                        INSERT INTO lsp_symbols(
                            repo_root, relative_path, content_hash, name, kind, line, end_line,
                            symbol_key, parent_symbol_key, depth, container_name, created_at
                        )
                        VALUES(
                            :repo_root, :relative_path, :content_hash, :name, :kind, :line, :end_line,
                            :symbol_key, :parent_symbol_key, :depth, :container_name, :created_at
                        )
                        """,
                        symbol_rows,
                    )

                conn.execute(
                    """
                    DELETE FROM lsp_call_relations
                    WHERE repo_root = :repo_root
                      AND relative_path = :relative_path
                    """,
                    {"repo_root": item.repo_root, "relative_path": item.relative_path},
                )
                relation_rows: list[dict[str, object]] = []
                for relation in self._dedupe_relations(item.relations):
                    relation_rows.append(
                        {
                            "repo_root": item.repo_root,
                            "relative_path": item.relative_path,
                            "content_hash": item.content_hash,
                            "from_symbol": str(relation.get("from_symbol", "")),
                            "to_symbol": str(relation.get("to_symbol", "")),
                            "line": int(relation.get("line", 0)),
                            "created_at": item.created_at,
                        }
                    )
                if len(relation_rows) > 0:
                    conn.executemany(
                        """
                        INSERT INTO lsp_call_relations(
                            repo_root, relative_path, content_hash, from_symbol, to_symbol, line, created_at
                        )
                        VALUES(
                            :repo_root, :relative_path, :content_hash, :from_symbol, :to_symbol, :line, :created_at
                        )
                        """,
                        relation_rows,
                    )
            if owned_conn:
                conn.commit()
        finally:
            if owned_conn:
                conn.close()

    def _dedupe_symbols(self, symbols: list[dict[str, object]]) -> list[dict[str, object]]:
        """심볼 고유키 기준으로 중복 항목을 제거한다."""
        deduped: list[dict[str, object]] = []
        seen_legacy: set[tuple[str, str, int, int]] = set()
        seen_symbol_key: set[str] = set()
        for symbol in symbols:
            symbol_key = _optional_str(symbol.get("symbol_key"))
            if symbol_key is not None:
                if symbol_key in seen_symbol_key:
                    continue
                seen_symbol_key.add(symbol_key)
                deduped.append(symbol)
                continue
            name = str(symbol.get("name", ""))
            kind = str(symbol.get("kind", ""))
            line = int(symbol.get("line", 0))
            end_line = int(symbol.get("end_line", 0))
            key = (name, kind, line, end_line)
            if key in seen_legacy:
                continue
            seen_legacy.add(key)
            deduped.append(symbol)
        return deduped

    def _dedupe_relations(self, relations: list[dict[str, object]]) -> list[dict[str, object]]:
        """호출 관계 고유키 기준으로 중복 항목을 제거한다."""
        deduped: list[dict[str, object]] = []
        seen: set[tuple[str, str, int]] = set()
        for relation in relations:
            from_symbol = str(relation.get("from_symbol", ""))
            to_symbol = str(relation.get("to_symbol", ""))
            line = int(relation.get("line", 0))
            key = (from_symbol, to_symbol, line)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(relation)
        return deduped

    def count_symbols(self, repo_root: str, relative_path: str, content_hash: str) -> int:
        """파일의 심볼 레코드 수를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS symbol_count
                FROM lsp_symbols
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchone()
        if row is None:
            return 0
        return row_int(row, "symbol_count")

    def count_distinct_symbol_files(self) -> int:
        """심볼이 저장된 distinct 파일 수를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS file_count
                FROM (
                    SELECT DISTINCT repo_root, relative_path
                    FROM lsp_symbols
                )
                """
            ).fetchone()
        if row is None:
            return 0
        return row_int(row, "file_count")

    def count_relations(self, repo_root: str, relative_path: str, content_hash: str) -> int:
        """파일의 호출 관계 레코드 수를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS relation_count
                FROM lsp_call_relations
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchone()
        if row is None:
            return 0
        return row_int(row, "relation_count")

    def search_symbols(
        self,
        repo_root: str,
        query: str,
        limit: int,
        path_prefix: str | None = None,
    ) -> list[SymbolSearchItemDTO]:
        """저장된 심볼 인덱스에서 이름 기준 검색 결과를 반환한다."""
        query_limit = max(1, int(limit))
        batch_limit = max(64, min(1024, query_limit * 4))
        params: dict[str, object] = {"repo_root": repo_root, "query_like": f"%{query}%"}
        where_prefix = ""
        if path_prefix is not None and path_prefix.strip() != "":
            where_prefix = "AND relative_path LIKE :path_prefix"
            params["path_prefix"] = f"{path_prefix.strip()}%"
        sql = f"""
            SELECT repo_root, relative_path, name, kind, line, end_line, content_hash,
                   symbol_key, parent_symbol_key, depth, container_name
            FROM lsp_symbols
            WHERE (
                    repo_root = :repo_root
                    OR repo_root IN (
                        SELECT DISTINCT repo_root
                        FROM collected_files_l1
                        WHERE scope_repo_root = :repo_root
                          AND is_deleted = 0
                    )
                )
              AND name LIKE :query_like
              {where_prefix}
            ORDER BY relative_path ASC, line ASC, name ASC, repo_root ASC
            LIMIT :batch_limit OFFSET :batch_offset
            """
        return self._search_symbols_with_dedupe_limit(
            sql=sql,
            params=params,
            requested_repo_root=repo_root,
            limit=query_limit,
            batch_limit=batch_limit,
        )

    def find_callers(self, repo_root: str, symbol_name: str, limit: int) -> list[CallerEdgeDTO]:
        """지정 심볼을 대상으로 호출자 관계를 조회한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT repo_root, relative_path, from_symbol, to_symbol, line, content_hash
                FROM lsp_call_relations
                WHERE (
                        repo_root = :repo_root
                        OR repo_root IN (
                            SELECT DISTINCT repo_root
                            FROM collected_files_l1
                            WHERE scope_repo_root = :repo_root
                              AND is_deleted = 0
                        )
                    )
                  AND to_symbol = :to_symbol
                ORDER BY relative_path ASC, line ASC
                LIMIT :limit
                """,
                {"repo_root": repo_root, "to_symbol": symbol_name, "limit": limit},
            ).fetchall()

        return [
            CallerEdgeDTO(
                repo=row_str(row, "repo_root"),
                relative_path=row_str(row, "relative_path"),
                from_symbol=row_str(row, "from_symbol"),
                to_symbol=row_str(row, "to_symbol"),
                line=row_int(row, "line"),
                content_hash=row_str(row, "content_hash"),
            )
            for row in rows
        ]

    def find_callees(self, repo_root: str, symbol_name: str, limit: int) -> list[CallerEdgeDTO]:
        """지정 심볼이 호출하는 대상 관계를 조회한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT repo_root, relative_path, from_symbol, to_symbol, line, content_hash
                FROM lsp_call_relations
                WHERE (
                        repo_root = :repo_root
                        OR repo_root IN (
                            SELECT DISTINCT repo_root
                            FROM collected_files_l1
                            WHERE scope_repo_root = :repo_root
                              AND is_deleted = 0
                        )
                    )
                  AND from_symbol = :from_symbol
                ORDER BY relative_path ASC, line ASC
                LIMIT :limit
                """,
                {"repo_root": repo_root, "from_symbol": symbol_name, "limit": limit},
            ).fetchall()
        return [
            CallerEdgeDTO(
                repo=row_str(row, "repo_root"),
                relative_path=row_str(row, "relative_path"),
                from_symbol=row_str(row, "from_symbol"),
                to_symbol=row_str(row, "to_symbol"),
                line=row_int(row, "line"),
                content_hash=row_str(row, "content_hash"),
            )
            for row in rows
        ]

    def find_implementations(self, repo_root: str, symbol_name: str, limit: int) -> list[SymbolSearchItemDTO]:
        """이름 기준 구현 후보 심볼 목록을 조회한다."""
        query_limit = max(1, int(limit))
        batch_limit = max(64, min(1024, query_limit * 4))
        sql = """
            SELECT repo_root, relative_path, name, kind, line, end_line, content_hash,
                   symbol_key, parent_symbol_key, depth, container_name
            FROM lsp_symbols
            WHERE (
                    repo_root = :repo_root
                    OR repo_root IN (
                        SELECT DISTINCT repo_root
                        FROM collected_files_l1
                        WHERE scope_repo_root = :repo_root
                          AND is_deleted = 0
                    )
                )
              AND name LIKE :name_like
            ORDER BY relative_path ASC, line ASC, name ASC, repo_root ASC
            LIMIT :batch_limit OFFSET :batch_offset
            """
        return self._search_symbols_with_dedupe_limit(
            sql=sql,
            params={"repo_root": repo_root, "name_like": f"%{symbol_name}%"},
            requested_repo_root=repo_root,
            limit=query_limit,
            batch_limit=batch_limit,
        )

    def _search_symbols_with_dedupe_limit(
        self,
        *,
        sql: str,
        params: dict[str, object],
        requested_repo_root: str,
        limit: int,
        batch_limit: int,
    ) -> list[SymbolSearchItemDTO]:
        """배치 조회 후 dedupe 결과에서 limit만큼만 반환한다."""
        offset = 0
        picked: dict[tuple[object, ...], SymbolSearchItemDTO] = {}
        with connect(self._db_path) as conn:
            while True:
                batch_params = dict(params)
                batch_params["batch_limit"] = int(batch_limit)
                batch_params["batch_offset"] = int(offset)
                rows = conn.execute(sql, batch_params).fetchall()
                if len(rows) == 0:
                    break
                requested_root = requested_repo_root.strip()
                for item in self._rows_to_symbol_items(rows):
                    dedupe_key = self._symbol_item_dedupe_key(item)
                    existing = picked.get(dedupe_key)
                    if existing is None:
                        picked[dedupe_key] = item
                        continue
                    existing_rank = self._symbol_item_rank(existing=existing, requested_root=requested_root)
                    candidate_rank = self._symbol_item_rank(existing=item, requested_root=requested_root)
                    if candidate_rank < existing_rank:
                        picked[dedupe_key] = item
                if len(picked) >= limit:
                    # head 내 non-requested 항목은 requested_root 직접 조회로 승격 여부를 확정한 뒤 종료한다.
                    while True:
                        changed = False
                        head = sorted(
                            picked.values(),
                            key=lambda it: (it.relative_path, it.line, it.name, it.repo),
                        )[:limit]
                        for item in head:
                            if item.repo == requested_root:
                                continue
                            promoted = self._find_requested_root_candidate_for_item(
                                conn=conn,
                                requested_root=requested_root,
                                item=item,
                            )
                            if promoted is None:
                                continue
                            dedupe_key = self._symbol_item_dedupe_key(item)
                            picked[dedupe_key] = promoted
                            changed = True
                        if not changed:
                            break
                    head = sorted(
                        picked.values(),
                        key=lambda it: self._symbol_item_sort_key(it),
                    )[:limit]
                    if len(head) == limit:
                        worst_head_key = self._symbol_item_sort_key(head[-1])
                        last_batch_row = rows[-1]
                        last_batch_key = (
                            row_str(last_batch_row, "relative_path"),
                            row_int(last_batch_row, "line"),
                            row_str(last_batch_row, "name"),
                            row_str(last_batch_row, "repo_root"),
                        )
                        if last_batch_key > worst_head_key:
                            break
                if len(rows) < batch_limit:
                    break
                offset += len(rows)
        deduped = sorted(
            picked.values(),
            key=lambda it: (it.relative_path, it.line, it.name, it.repo),
        )
        return deduped[:limit]

    def _rows_to_symbol_items(self, rows: list[object]) -> list[SymbolSearchItemDTO]:
        """DB row를 SymbolSearchItemDTO 목록으로 변환한다."""
        return [
            SymbolSearchItemDTO(
                repo=row_str(row, "repo_root"),
                relative_path=row_str(row, "relative_path"),
                name=row_str(row, "name"),
                kind=row_str(row, "kind"),
                line=row_int(row, "line"),
                end_line=row_int(row, "end_line"),
                content_hash=row_str(row, "content_hash"),
                symbol_key=row["symbol_key"] if row["symbol_key"] is None or isinstance(row["symbol_key"], str) else None,
                parent_symbol_key=row["parent_symbol_key"] if row["parent_symbol_key"] is None or isinstance(row["parent_symbol_key"], str) else None,
                depth=row_int(row, "depth"),
                container_name=row["container_name"] if row["container_name"] is None or isinstance(row["container_name"], str) else None,
            )
            for row in rows
        ]

    def _dedupe_symbol_search_items(
        self,
        *,
        items: list[SymbolSearchItemDTO],
        requested_repo_root: str,
    ) -> list[SymbolSearchItemDTO]:
        """동일 절대 파일/심볼 중복을 제거하고 요청 repo_root를 우선한다."""
        picked: dict[tuple[object, ...], SymbolSearchItemDTO] = {}
        requested_root = requested_repo_root.strip()
        for item in items:
            dedupe_key = self._symbol_item_dedupe_key(item)
            existing = picked.get(dedupe_key)
            if existing is None:
                picked[dedupe_key] = item
                continue
            existing_rank = self._symbol_item_rank(existing=existing, requested_root=requested_root)
            candidate_rank = self._symbol_item_rank(existing=item, requested_root=requested_root)
            if candidate_rank < existing_rank:
                picked[dedupe_key] = item
        return sorted(
            picked.values(),
            key=lambda it: (it.relative_path, it.line, it.name, it.repo),
        )

    def _symbol_item_dedupe_key(self, item: SymbolSearchItemDTO) -> tuple[object, ...]:
        file_key = _canonical_file_key(repo_root=item.repo, relative_path=item.relative_path)
        # symbol_key는 repo_root를 포함해 생성될 수 있어 cross-root dedupe 키로 부적합하다.
        symbol_discriminator = (
            item.name,
            item.kind,
            item.line,
            item.end_line,
            item.depth,
            item.container_name or "",
        )
        return (file_key, symbol_discriminator)

    def _find_requested_root_candidate_for_item(
        self,
        *,
        conn: object,
        requested_root: str,
        item: SymbolSearchItemDTO,
    ) -> SymbolSearchItemDTO | None:
        """현재 항목과 동일 dedupe 키를 갖는 requested_root 후보를 조회한다."""
        req_root = requested_root.strip()
        if req_root == "":
            return None
        file_key = _canonical_file_key(repo_root=item.repo, relative_path=item.relative_path)
        root_prefix = req_root.rstrip("/") + "/"
        if not file_key.startswith(root_prefix):
            return None
        candidate_relative = file_key[len(root_prefix) :]
        row = conn.execute(
            """
            SELECT repo_root, relative_path, name, kind, line, end_line, content_hash,
                   symbol_key, parent_symbol_key, depth, container_name
            FROM lsp_symbols
            WHERE repo_root = :repo_root
              AND relative_path = :relative_path
              AND name = :name
              AND kind = :kind
              AND line = :line
              AND end_line = :end_line
              AND depth = :depth
              AND COALESCE(container_name, '') = :container_name
            LIMIT 1
            """,
            {
                "repo_root": req_root,
                "relative_path": candidate_relative,
                "name": item.name,
                "kind": item.kind,
                "line": item.line,
                "end_line": item.end_line,
                "depth": item.depth,
                "container_name": item.container_name or "",
            },
        ).fetchone()
        if row is None:
            return None
        return SymbolSearchItemDTO(
            repo=row_str(row, "repo_root"),
            relative_path=row_str(row, "relative_path"),
            name=row_str(row, "name"),
            kind=row_str(row, "kind"),
            line=row_int(row, "line"),
            end_line=row_int(row, "end_line"),
            content_hash=row_str(row, "content_hash"),
            symbol_key=row["symbol_key"] if row["symbol_key"] is None or isinstance(row["symbol_key"], str) else None,
            parent_symbol_key=row["parent_symbol_key"] if row["parent_symbol_key"] is None or isinstance(row["parent_symbol_key"], str) else None,
            depth=row_int(row, "depth"),
            container_name=row["container_name"] if row["container_name"] is None or isinstance(row["container_name"], str) else None,
        )

    def _symbol_item_rank(self, *, existing: SymbolSearchItemDTO, requested_root: str) -> tuple[int, int, int]:
        """중복 후보 선택 우선순위를 계산한다."""
        exact_repo_match = 0 if existing.repo == requested_root else 1
        relative_depth = existing.relative_path.count("/")
        repo_depth = existing.repo.count("/")
        return (exact_repo_match, relative_depth, repo_depth)

    def _symbol_item_sort_key(self, item: SymbolSearchItemDTO) -> tuple[str, int, str, str]:
        return (item.relative_path, item.line, item.name, item.repo)

    def get_repo_call_graph_health(self, repo_root: str) -> dict[str, int]:
        """저장소 단위 호출 그래프 건강 지표를 반환한다."""
        with connect(self._db_path) as conn:
            symbol_row = conn.execute(
                """
                SELECT COUNT(*) AS symbol_count
                FROM lsp_symbols
                WHERE (
                        repo_root = :repo_root
                        OR repo_root IN (
                            SELECT DISTINCT repo_root
                            FROM collected_files_l1
                            WHERE scope_repo_root = :repo_root
                              AND is_deleted = 0
                        )
                    )
                """,
                {"repo_root": repo_root},
            ).fetchone()
            relation_row = conn.execute(
                """
                SELECT COUNT(*) AS relation_count
                FROM lsp_call_relations
                WHERE (
                        repo_root = :repo_root
                        OR repo_root IN (
                            SELECT DISTINCT repo_root
                            FROM collected_files_l1
                            WHERE scope_repo_root = :repo_root
                              AND is_deleted = 0
                        )
                    )
                """,
                {"repo_root": repo_root},
            ).fetchone()
            orphan_row = conn.execute(
                """
                SELECT COUNT(*) AS orphan_relation_count
                FROM lsp_call_relations rel
                WHERE (
                        rel.repo_root = :repo_root
                        OR rel.repo_root IN (
                            SELECT DISTINCT repo_root
                            FROM collected_files_l1
                            WHERE scope_repo_root = :repo_root
                              AND is_deleted = 0
                        )
                    )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM lsp_symbols sym
                    WHERE sym.repo_root = rel.repo_root
                      AND sym.relative_path = rel.relative_path
                      AND sym.content_hash = rel.content_hash
                  )
                """,
                {"repo_root": repo_root},
            ).fetchone()
        return {
            "symbol_count": 0 if symbol_row is None else row_int(symbol_row, "symbol_count"),
            "relation_count": 0 if relation_row is None else row_int(relation_row, "relation_count"),
            "orphan_relation_count": 0 if orphan_row is None else row_int(orphan_row, "orphan_relation_count"),
        }

    def count_distinct_callers(self, repo_root: str, symbol_name: str) -> int:
        """심볼을 참조하는 서로 다른 파일 수를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT (repo_root || '::' || relative_path)) AS caller_file_count
                FROM lsp_call_relations
                WHERE (
                        repo_root = :repo_root
                        OR repo_root IN (
                            SELECT DISTINCT repo_root
                            FROM collected_files_l1
                            WHERE scope_repo_root = :repo_root
                              AND is_deleted = 0
                        )
                    )
                  AND to_symbol = :to_symbol
                """,
                {"repo_root": repo_root, "to_symbol": symbol_name},
            ).fetchone()
        if row is None:
            return 0
        return row_int(row, "caller_file_count")

    def list_file_symbols(self, repo_root: str, relative_path: str, content_hash: str) -> list[dict[str, object]]:
        """파일 단위 심볼 집합을 반환한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT name, kind, line, end_line
                FROM lsp_symbols
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                ORDER BY line ASC, name ASC
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchall()
        return [
            {
                "name": row_str(row, "name"),
                "kind": row_str(row, "kind"),
                "line": row_int(row, "line"),
                "end_line": row_int(row, "end_line"),
            }
            for row in rows
        ]

    def list_file_symbols_full(self, repo_root: str, relative_path: str, content_hash: str) -> list[dict[str, object]]:
        """파일 단위 심볼 집합(확장 메타 포함)을 반환한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT name, kind, line, end_line, symbol_key, parent_symbol_key, depth, container_name
                FROM lsp_symbols
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                ORDER BY line ASC, name ASC
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchall()
        return [
            {
                "name": row_str(row, "name"),
                "kind": row_str(row, "kind"),
                "line": row_int(row, "line"),
                "end_line": row_int(row, "end_line"),
                "symbol_key": row_str(row, "symbol_key") or None,
                "parent_symbol_key": row_str(row, "parent_symbol_key") or None,
                "depth": row_int(row, "depth"),
                "container_name": row_str(row, "container_name") or None,
            }
            for row in rows
        ]

    def list_file_symbols_latest(self, repo_root: str, relative_path: str) -> list[dict[str, object]]:
        """content_hash 불일치 상황에서도 파일 최신 심볼 집합을 반환한다.

        품질 비교(offline/report) 경로에서만 사용한다. 실시간 read/search 경로는
        content_hash 일치 강제 조회(list_file_symbols)를 그대로 사용해야 한다.
        """
        with connect(self._db_path) as conn:
            latest = conn.execute(
                """
                SELECT content_hash
                FROM lsp_symbols
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                ORDER BY created_at DESC
                LIMIT 1
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                },
            ).fetchone()
        if latest is None:
            return []
        return self.list_file_symbols(repo_root, relative_path, row_str(latest, "content_hash"))

    def list_file_relations(self, repo_root: str, relative_path: str, content_hash: str) -> list[dict[str, object]]:
        """파일 단위 호출 관계 집합을 반환한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT from_symbol, to_symbol, line
                FROM lsp_call_relations
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                ORDER BY line ASC, from_symbol ASC, to_symbol ASC
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchall()
        return [
            {
                "from_symbol": row_str(row, "from_symbol"),
                "to_symbol": row_str(row, "to_symbol"),
                "line": row_int(row, "line"),
            }
            for row in rows
        ]

    def list_file_relations_latest(self, repo_root: str, relative_path: str) -> list[dict[str, object]]:
        """content_hash 불일치 상황에서도 파일 최신 호출 관계 집합을 반환한다."""
        with connect(self._db_path) as conn:
            latest = conn.execute(
                """
                SELECT content_hash
                FROM lsp_call_relations
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                ORDER BY created_at DESC
                LIMIT 1
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                },
            ).fetchone()
        if latest is None:
            return []
        return self.list_file_relations(repo_root, relative_path, row_str(latest, "content_hash"))
