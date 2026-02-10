from sari.core.utils.context import ContextBudgetEngine

def test_context_budget_filter_hits():
    engine = ContextBudgetEngine(max_tokens=1000)
    
    # Under 20 hits - no change
    hits = [{"path": f"path{i}", "repo": "repo", "snippet": "content"} for i in range(10)]
    filtered = engine.filter_hits(hits)
    assert len(filtered) == 10
    assert filtered[0]["snippet"] == "content"
    
    # Over 20 hits - summarized
    hits_many = [{"path": f"path{i}", "repo": "repo", "snippet": "A" * 300} for i in range(25)]
    filtered_many = engine.filter_hits(hits_many)
    assert len(filtered_many) == 25
    assert filtered_many[0]["is_truncated"] is True
    assert len(filtered_many[0]["summary"]) == 203 # 200 + "..."
    assert filtered_many[0]["summary"].endswith("...")
