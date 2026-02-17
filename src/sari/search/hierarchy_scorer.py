"""심볼 계층 점수 계산기를 구현한다."""

from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import SearchItemDTO


@dataclass(frozen=True)
class HierarchyScorePolicyDTO:
    """계층 점수 계산 정책을 표현한다."""

    depth_decay_base: float = 1.0
    parent_bonus: float = 0.2
    container_match_bonus: float = 0.3
    max_hierarchy_boost: float = 5.0


class HierarchyScorer:
    """symbol 계층 메타를 기반으로 보조 점수를 계산한다."""

    def __init__(self, policy: HierarchyScorePolicyDTO | None = None) -> None:
        """점수 정책을 주입한다."""
        self._policy = policy if policy is not None else HierarchyScorePolicyDTO()

    def apply(self, items: list[SearchItemDTO], query: str) -> list[SearchItemDTO]:
        """검색 아이템에 계층 점수를 계산해 반영한다."""
        normalized_query = query.strip().lower()
        output: list[SearchItemDTO] = []
        for item in items:
            hierarchy_score = self._compute_hierarchy_score(item=item, normalized_query=normalized_query)
            output.append(
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
                    hierarchy_score=hierarchy_score,
                    hierarchy_norm_score=item.hierarchy_norm_score,
                    symbol_key=item.symbol_key,
                    parent_symbol_key=item.parent_symbol_key,
                    depth=item.depth,
                    container_name=item.container_name,
                    ranking_components=item.ranking_components,
                    vector_score=item.vector_score,
                    blended_score=item.blended_score,
                    final_score=item.final_score,
                )
            )
        return output

    def _compute_hierarchy_score(self, item: SearchItemDTO, normalized_query: str) -> float:
        """계층/컨테이너 맥락 점수를 계산한다."""
        if item.item_type != "symbol":
            return 0.0
        if item.symbol_key is None or item.symbol_key.strip() == "":
            return 0.0
        depth_penalty = 1.0 / (self._policy.depth_decay_base + float(max(0, item.depth)))
        score = depth_penalty
        if item.parent_symbol_key is not None and item.parent_symbol_key.strip() != "":
            score += self._policy.parent_bonus
        if normalized_query != "" and item.container_name is not None:
            if normalized_query in item.container_name.lower():
                score += self._policy.container_match_bonus
        return min(score, self._policy.max_hierarchy_boost)
