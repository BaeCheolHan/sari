"""검색 점수 결합 책임을 분리한 모듈."""

from __future__ import annotations

from sari.core.models import RankingComponentsDTO, SearchItemDTO


class ScoreBlender:
    """RRF 융합과 최종 점수 결합을 담당한다."""

    def __init__(self, rrf_k: int, w_rrf: float, w_importance: float, w_vector: float, w_hierarchy: float) -> None:
        """점수 결합 정책 파라미터를 초기화한다."""
        self._rrf_k = rrf_k
        self._w_rrf = w_rrf
        self._w_importance = w_importance
        self._w_vector = w_vector
        self._w_hierarchy = w_hierarchy

    def fuse_rrf(
        self,
        candidate_items: list[SearchItemDTO],
        resolved_items: list[SearchItemDTO],
        limit: int,
    ) -> list[SearchItemDTO]:
        """후보와 심볼 결과를 RRF로 융합한다."""
        score_map: dict[str, float] = {}
        item_map: dict[str, SearchItemDTO] = {}

        for rank, item in enumerate(candidate_items, start=1):
            key = self._item_key(item)
            item_map[key] = item
            score_map[key] = score_map.get(key, 0.0) + (1.0 / float(self._rrf_k + rank))

        for rank, item in enumerate(resolved_items, start=1):
            key = self._item_key(item)
            item_map[key] = item
            score_map[key] = score_map.get(key, 0.0) + (1.0 / float(self._rrf_k + rank))

        sorted_keys = sorted(score_map.keys(), key=lambda key: score_map[key], reverse=True)
        output: list[SearchItemDTO] = []
        for key in sorted_keys[:limit]:
            raw_item = item_map[key]
            output.append(
                SearchItemDTO(
                    item_type=raw_item.item_type,
                    repo=raw_item.repo,
                    relative_path=raw_item.relative_path,
                    score=score_map[key],
                    source=raw_item.source,
                    name=raw_item.name,
                    kind=raw_item.kind,
                    content_hash=raw_item.content_hash,
                    rrf_score=score_map[key],
                    importance_score=raw_item.importance_score,
                    base_rrf_score=score_map[key],
                    importance_norm_score=0.0,
                    vector_norm_score=0.0,
                    hierarchy_score=raw_item.hierarchy_score,
                    hierarchy_norm_score=0.0,
                    symbol_key=raw_item.symbol_key,
                    parent_symbol_key=raw_item.parent_symbol_key,
                    depth=raw_item.depth,
                    container_name=raw_item.container_name,
                    ranking_components=None,
                    vector_score=raw_item.vector_score,
                    blended_score=score_map[key],
                    final_score=score_map[key],
                )
            )
        return output

    def blend(self, items: list[SearchItemDTO], limit: int) -> list[SearchItemDTO]:
        """RRF/importance/vector를 정규화해 단일 점수로 결합한다."""
        if len(items) == 0:
            return []
        rrf_values = [item.rrf_score if item.rrf_score > 0.0 else item.score for item in items]
        importance_values = [item.importance_score for item in items]
        vector_values = [item.vector_score if item.vector_score is not None else 0.0 for item in items]
        hierarchy_values = [item.hierarchy_score for item in items]
        rrf_norm = self._normalize_minmax(rrf_values)
        importance_norm = self._normalize_minmax(importance_values)
        vector_norm = self._normalize_minmax(vector_values)
        hierarchy_norm = self._normalize_minmax(hierarchy_values)

        blended: list[SearchItemDTO] = []
        for index, item in enumerate(items):
            score = (
                (self._w_rrf * rrf_norm[index])
                + (self._w_importance * importance_norm[index])
                + (self._w_vector * vector_norm[index])
                + (self._w_hierarchy * hierarchy_norm[index])
            )
            blended.append(
                SearchItemDTO(
                    item_type=item.item_type,
                    repo=item.repo,
                    relative_path=item.relative_path,
                    score=score,
                    source=item.source,
                    name=item.name,
                    kind=item.kind,
                    content_hash=item.content_hash,
                    rrf_score=rrf_values[index],
                    importance_score=item.importance_score,
                    base_rrf_score=rrf_values[index],
                    importance_norm_score=importance_norm[index],
                    vector_norm_score=vector_norm[index],
                    hierarchy_score=item.hierarchy_score,
                    hierarchy_norm_score=hierarchy_norm[index],
                    symbol_key=item.symbol_key,
                    parent_symbol_key=item.parent_symbol_key,
                    depth=item.depth,
                    container_name=item.container_name,
                    ranking_components=RankingComponentsDTO(
                        rrf=rrf_norm[index],
                        importance=importance_norm[index],
                        vector=vector_norm[index],
                        hierarchy=hierarchy_norm[index],
                        final=score,
                    ),
                    vector_score=item.vector_score,
                    blended_score=score,
                    final_score=score,
                )
            )
        blended.sort(key=lambda item: item.score, reverse=True)
        return blended[:limit]

    def _normalize_minmax(self, values: list[float]) -> list[float]:
        """0..1 범위 min-max 정규화를 수행한다."""
        if len(values) == 0:
            return []
        minimum = min(values)
        maximum = max(values)
        if minimum == maximum:
            return [0.0 for _ in values]
        scale = maximum - minimum
        return [float((value - minimum) / scale) for value in values]

    def _item_key(self, item: SearchItemDTO) -> str:
        """RRF 융합용 아이템 고유 키를 계산한다."""
        if item.item_type == "file":
            return f"file:{item.repo}:{item.relative_path}"
        if item.symbol_key is not None and item.symbol_key.strip() != "":
            return f"symbol-key:{item.repo}:{item.symbol_key}"
        return f"symbol:{item.repo}:{item.relative_path}:{item.name}:{item.kind}"
