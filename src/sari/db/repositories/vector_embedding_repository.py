"""벡터 임베딩 저장소를 구현한다."""

from __future__ import annotations

import json
from pathlib import Path

from sari.db.row_mapper import row_str
from sari.db.schema import connect


class VectorEmbeddingRepository:
    """파일/질의 벡터 임베딩 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def upsert_file_embedding(
        self,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        model_id: str,
        dim: int,
        vector: list[float],
        updated_at: str,
    ) -> None:
        """파일 임베딩을 업서트한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO file_embeddings(
                    repo_root, scope_repo_root, relative_path, content_hash, model_id, dim, vector_json, updated_at
                )
                VALUES(
                    :repo_root, :scope_repo_root, :relative_path, :content_hash, :model_id, :dim, :vector_json, :updated_at
                )
                ON CONFLICT(repo_root, relative_path, content_hash, model_id) DO UPDATE SET
                    scope_repo_root = excluded.scope_repo_root,
                    dim = excluded.dim,
                    vector_json = excluded.vector_json,
                    updated_at = excluded.updated_at
                """,
                {
                    "repo_root": repo_root,
                    "scope_repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                    "model_id": model_id,
                    "dim": dim,
                    "vector_json": json.dumps(vector, ensure_ascii=False),
                    "updated_at": updated_at,
                },
            )
            conn.commit()

    def get_file_embedding(
        self,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        model_id: str,
    ) -> list[float] | None:
        """파일 임베딩을 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT vector_json
                FROM file_embeddings
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                  AND model_id = :model_id
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                    "model_id": model_id,
                },
            ).fetchone()
        if row is None:
            return None
        loaded = json.loads(row_str(row, "vector_json"))
        if not isinstance(loaded, list):
            return None
        output: list[float] = []
        for value in loaded:
            if isinstance(value, (int, float)):
                output.append(float(value))
        return output

    def upsert_query_embedding(self, query_hash: str, model_id: str, dim: int, vector: list[float], updated_at: str) -> None:
        """질의 임베딩을 업서트한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO query_embeddings(query_hash, model_id, dim, vector_json, updated_at)
                VALUES(:query_hash, :model_id, :dim, :vector_json, :updated_at)
                ON CONFLICT(query_hash, model_id) DO UPDATE SET
                    dim = excluded.dim,
                    vector_json = excluded.vector_json,
                    updated_at = excluded.updated_at
                """,
                {
                    "query_hash": query_hash,
                    "model_id": model_id,
                    "dim": dim,
                    "vector_json": json.dumps(vector, ensure_ascii=False),
                    "updated_at": updated_at,
                },
            )
            conn.commit()

    def get_query_embedding(self, query_hash: str, model_id: str) -> list[float] | None:
        """질의 임베딩을 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT vector_json
                FROM query_embeddings
                WHERE query_hash = :query_hash
                  AND model_id = :model_id
                """,
                {"query_hash": query_hash, "model_id": model_id},
            ).fetchone()
        if row is None:
            return None
        loaded = json.loads(row_str(row, "vector_json"))
        if not isinstance(loaded, list):
            return None
        output: list[float] = []
        for value in loaded:
            if isinstance(value, (int, float)):
                output.append(float(value))
        return output
