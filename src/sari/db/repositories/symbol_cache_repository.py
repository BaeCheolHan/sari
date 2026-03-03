"""심볼 검색 캐시 저장소를 구현한다."""

from __future__ import annotations

import json
from pathlib import Path

from sari.core.exceptions import ErrorContext, ValidationError
from sari.core.models import SearchItemDTO
from sari.db.row_mapper import row_str
from sari.db.schema import connect


class SymbolCacheRepository:
    """파일/질의 기반 심볼 캐시를 저장한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def get_cached_items(
        self,
        repo_root: str,
        relative_path: str,
        query: str,
        file_hash: str,
        cache_variant: str = "list",
    ) -> list[SearchItemDTO] | None:
        """유효한 캐시 항목을 조회한다."""
        normalized_variant = _normalize_cache_variant(cache_variant)
        query_key = _cache_query_key(query, normalized_variant)
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT items_json
                FROM lsp_symbol_cache
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND query = :query
                  AND file_hash = :file_hash
                  AND invalidated = 0
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "query": query_key,
                    "file_hash": file_hash,
                },
            ).fetchone()
            if row is None and normalized_variant == "list":
                # 하위호환: variant 도입 전 레거시 키(query 원문)를 우선 재사용한다.
                row = conn.execute(
                    """
                    SELECT items_json
                    FROM lsp_symbol_cache
                    WHERE repo_root = :repo_root
                      AND relative_path = :relative_path
                      AND query = :query
                      AND file_hash = :file_hash
                      AND invalidated = 0
                    """,
                    {
                        "repo_root": repo_root,
                        "relative_path": relative_path,
                        "query": query,
                        "file_hash": file_hash,
                    },
                ).fetchone()
        if row is None:
            return None

        loaded = json.loads(row_str(row, "items_json"))
        if not isinstance(loaded, list):
            raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message="items_json must be list"))
        items: list[SearchItemDTO] = []
        for raw in loaded:
            if not isinstance(raw, dict):
                raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message="items_json element must be object"))
            items.append(
                SearchItemDTO(
                    item_type=str(raw["item_type"]),
                    repo=str(raw["repo"]),
                    relative_path=str(raw["relative_path"]),
                    score=float(raw["score"]),
                    source=str(raw["source"]),
                    name=str(raw["name"]) if raw["name"] is not None else None,
                    kind=str(raw["kind"]) if raw["kind"] is not None else None,
                    symbol_info=str(raw["symbol_info"]) if raw.get("symbol_info") is not None else None,
                    content_hash=str(raw["content_hash"]) if raw.get("content_hash") is not None else None,
                    rrf_score=float(raw.get("rrf_score", 0.0)),
                    importance_score=float(raw.get("importance_score", 0.0)),
                    vector_score=float(raw["vector_score"]) if raw.get("vector_score") is not None else None,
                    final_score=float(raw.get("final_score", raw["score"])),
                )
            )
        return items

    def upsert_items(
        self,
        repo_root: str,
        relative_path: str,
        query: str,
        file_hash: str,
        items: list[SearchItemDTO],
        cache_variant: str = "list",
    ) -> None:
        """캐시 항목을 업서트한다."""
        normalized_variant = _normalize_cache_variant(cache_variant)
        query_key = _cache_query_key(query, normalized_variant)
        payload = [
            {
                "item_type": item.item_type,
                "repo": item.repo,
                "relative_path": item.relative_path,
                "score": item.score,
                "source": item.source,
                "name": item.name,
                "kind": item.kind,
                "symbol_info": item.symbol_info,
                "content_hash": item.content_hash,
                "rrf_score": item.rrf_score,
                "importance_score": item.importance_score,
                "vector_score": item.vector_score,
                "final_score": item.final_score if item.final_score != 0.0 else item.score,
            }
            for item in items
        ]
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO lsp_symbol_cache(repo_root, relative_path, query, file_hash, items_json, invalidated, updated_at)
                VALUES(:repo_root, :relative_path, :query, :file_hash, :items_json, 0, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(repo_root, relative_path, query) DO UPDATE SET
                    file_hash = excluded.file_hash,
                    items_json = excluded.items_json,
                    invalidated = 0,
                    updated_at = excluded.updated_at
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "query": query_key,
                    "file_hash": file_hash,
                    "items_json": json.dumps(payload, ensure_ascii=False),
                },
            )
            conn.commit()

    def invalidate_path(self, repo_root: str, relative_path: str) -> None:
        """특정 파일 경로의 캐시를 무효화한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE lsp_symbol_cache
                SET invalidated = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                """,
                {"repo_root": repo_root, "relative_path": relative_path},
            )
            conn.commit()

    def invalidate_all(self) -> int:
        """전체 캐시를 무효화하고 영향 row 수를 반환한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE lsp_symbol_cache
                SET invalidated = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE invalidated = 0
                """
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)


def _normalize_cache_variant(cache_variant: str) -> str:
    normalized = cache_variant.strip().lower()
    if normalized in {"detail", "list"}:
        return normalized
    return "list"


def _cache_query_key(query: str, cache_variant: str) -> str:
    return f"{query}\n__cache_variant:{cache_variant}"
