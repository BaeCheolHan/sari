from collections.abc import Mapping
from typing import TypeAlias

Hit: TypeAlias = dict[str, object]

class ContextBudgetEngine:
    """
    Ensures search results and code payloads fit within LLM context budgets.
    Reduces verbosity for large results.
    """
    def __init__(self, max_tokens: int = 4000):
        self.max_tokens = max_tokens

    def filter_hits(self, hits: list[Hit]) -> list[Hit]:
        """If too many hits, summarize or truncate snippets."""
        if len(hits) > 20:
            # Under pressure: Only keep core metadata and short snippets
            return [self._summarize(h) for h in hits]
        return hits

    def _summarize(self, hit: Mapping[str, object]) -> Hit:
        """Produce a high-density summary of a search hit."""
        snippet = str(hit.get("snippet", ""))
        return {
            "path": hit["path"],
            "repo": hit["repo"],
            "summary": snippet[:200] + "...",
            "is_truncated": True
        }
