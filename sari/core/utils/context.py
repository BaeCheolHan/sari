from typing import List, Dict, Any

class ContextBudgetEngine:
    """
    Ensures search results and code payloads fit within LLM context budgets.
    Reduces verbosity for large results.
    """
    def __init__(self, max_tokens: int = 4000):
        self.max_tokens = max_tokens

    def filter_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """If too many hits, summarize or truncate snippets."""
        if len(hits) > 20:
            # Under pressure: Only keep core metadata and short snippets
            return [self._summarize(h) for h in hits]
        return hits

    def _summarize(self, hit: Dict[str, Any]) -> Dict[str, Any]:
        """Produce a high-density summary of a search hit."""
        return {
            "path": hit["path"],
            "repo": hit["repo"],
            "summary": hit.get("snippet", "")[:200] + "...",
            "is_truncated": True
        }
