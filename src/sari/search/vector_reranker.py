"""로컬 벡터 임베딩 기반 재정렬을 구현한다."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from sari.core.models import SearchItemDTO, now_iso8601_utc
from sari.db.repositories.vector_embedding_repository import VectorEmbeddingRepository


@dataclass(frozen=True)
class VectorConfigDTO:
    """벡터 재정렬 설정을 표현한다."""

    enabled: bool = False
    model_id: str = "hashbow-v1"
    dim: int = 128
    candidate_k: int = 50
    rerank_k: int = 20
    blend_weight: float = 0.2
    min_similarity_threshold: float = 0.15
    max_vector_boost: float = 0.2
    min_token_count_for_rerank: int = 2
    apply_to_item_types: tuple[str, ...] = ("symbol", "file")


@dataclass(frozen=True)
class VectorRerankStatsDTO:
    """벡터 재정렬 수행 통계를 표현한다."""

    applied_count: int = 0
    skipped_count: int = 0


class VectorIndexSink:
    """파일 임베딩 생성/갱신 인터페이스를 제공한다."""

    def __init__(self, repository: VectorEmbeddingRepository, config: VectorConfigDTO) -> None:
        """저장소와 벡터 설정을 주입한다."""
        self._repository = repository
        self._config = config

    def upsert_file_embedding(self, repo_root: str, relative_path: str, content_hash: str, content_text: str) -> None:
        """파일 본문으로 임베딩을 생성해 저장한다."""
        if not self._config.enabled:
            return
        vector = _embed_text(content_text, self._config.dim)
        self._repository.upsert_file_embedding(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            model_id=self._config.model_id,
            dim=self._config.dim,
            vector=vector,
            updated_at=now_iso8601_utc(),
        )


class VectorReranker:
    """질의/파일 임베딩 유사도로 검색 결과를 재정렬한다."""

    def __init__(self, repository: VectorEmbeddingRepository, config: VectorConfigDTO) -> None:
        """저장소와 벡터 설정을 주입한다."""
        self._repository = repository
        self._config = config
        self._last_stats = VectorRerankStatsDTO()

    @property
    def config(self) -> VectorConfigDTO:
        """현재 벡터 설정을 반환한다."""
        return self._config

    @property
    def last_stats(self) -> VectorRerankStatsDTO:
        """직전 벡터 재정렬 실행 통계를 반환한다."""
        return self._last_stats

    def rerank(self, items: list[SearchItemDTO], query: str, limit: int) -> list[SearchItemDTO]:
        """상위 후보에 벡터 신호를 반영하되 최종 점수 계산은 수행하지 않는다."""
        if not self._config.enabled:
            self._last_stats = VectorRerankStatsDTO()
            return items[:limit]
        query_tokens = re.findall(r"[a-zA-Z0-9_]+", query.lower())
        if len(query_tokens) < max(1, self._config.min_token_count_for_rerank):
            self._last_stats = VectorRerankStatsDTO(applied_count=0, skipped_count=min(len(items), max(1, self._config.candidate_k)))
            return items[:limit]
        query_vector = self._get_or_create_query_embedding(query)
        if query_vector is None:
            self._last_stats = VectorRerankStatsDTO(applied_count=0, skipped_count=min(len(items), max(1, self._config.candidate_k)))
            return items[:limit]

        candidate_window = max(1, min(self._config.candidate_k, self._config.rerank_k))
        scoring_items: list[SearchItemDTO] = []
        applied_count = 0
        skipped_count = 0
        for item in items[:candidate_window]:
            if item.item_type not in self._config.apply_to_item_types:
                scoring_items.append(item)
                skipped_count += 1
                continue
            if item.content_hash is None:
                scoring_items.append(item)
                skipped_count += 1
                continue
            file_vector = self._repository.get_file_embedding(
                repo_root=item.repo,
                relative_path=item.relative_path,
                content_hash=item.content_hash,
                model_id=self._config.model_id,
            )
            if file_vector is None:
                scoring_items.append(item)
                skipped_count += 1
                continue
            similarity = _cosine_similarity(query_vector, file_vector)
            if similarity < self._config.min_similarity_threshold:
                scoring_items.append(
                    SearchItemDTO(
                        item_type=item.item_type,
                        repo=item.repo,
                        relative_path=item.relative_path,
                        score=item.score,
                        source=item.source,
                        name=item.name,
                        kind=item.kind,
                        content_hash=item.content_hash,
                        rrf_score=item.rrf_score,
                        importance_score=item.importance_score,
                        base_rrf_score=item.base_rrf_score,
                        importance_norm_score=item.importance_norm_score,
                        vector_norm_score=item.vector_norm_score,
                        hierarchy_score=item.hierarchy_score,
                        hierarchy_norm_score=item.hierarchy_norm_score,
                        symbol_key=item.symbol_key,
                        parent_symbol_key=item.parent_symbol_key,
                        depth=item.depth,
                        container_name=item.container_name,
                        ranking_components=item.ranking_components,
                        vector_score=similarity,
                        blended_score=item.blended_score,
                        final_score=item.final_score,
                    )
                )
                skipped_count += 1
                continue
            scoring_items.append(
                SearchItemDTO(
                    item_type=item.item_type,
                    repo=item.repo,
                    relative_path=item.relative_path,
                    score=item.score,
                    source=item.source,
                    name=item.name,
                    kind=item.kind,
                    content_hash=item.content_hash,
                    rrf_score=item.rrf_score,
                    importance_score=item.importance_score,
                    base_rrf_score=item.base_rrf_score,
                    importance_norm_score=item.importance_norm_score,
                    vector_norm_score=item.vector_norm_score,
                    hierarchy_score=item.hierarchy_score,
                    hierarchy_norm_score=item.hierarchy_norm_score,
                    symbol_key=item.symbol_key,
                    parent_symbol_key=item.parent_symbol_key,
                    depth=item.depth,
                    container_name=item.container_name,
                    ranking_components=item.ranking_components,
                    vector_score=similarity,
                    blended_score=item.blended_score,
                    final_score=item.final_score,
                )
            )
            applied_count += 1

        remain = items[candidate_window:]
        merged = [*scoring_items, *remain]
        self._last_stats = VectorRerankStatsDTO(applied_count=applied_count, skipped_count=skipped_count)
        return merged[:limit]

    def _get_or_create_query_embedding(self, query: str) -> list[float] | None:
        """질의 임베딩을 캐시 조회 후 없으면 생성한다."""
        normalized = query.strip().lower()
        if normalized == "":
            return None
        query_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        cached = self._repository.get_query_embedding(query_hash=query_hash, model_id=self._config.model_id)
        if cached is not None:
            return cached
        vector = _embed_text(normalized, self._config.dim)
        self._repository.upsert_query_embedding(
            query_hash=query_hash,
            model_id=self._config.model_id,
            dim=self._config.dim,
            vector=vector,
            updated_at=now_iso8601_utc(),
        )
        return vector


def _embed_text(text: str, dim: int) -> list[float]:
    """텍스트를 해시 기반 bag-of-words 벡터로 변환한다."""
    vector = [0.0] * dim
    tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], byteorder="big") % dim
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        return vector
    return [value / norm for value in vector]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    """두 벡터의 코사인 유사도를 계산한다."""
    if len(left) == 0 or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right)))
