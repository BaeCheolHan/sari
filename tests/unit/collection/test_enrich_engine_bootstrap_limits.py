from __future__ import annotations

from sari.services.collection.enrich_engine import EnrichEngine


def test_bootstrap_steady_respects_limit_cap_for_l2_l3_split() -> None:
    engine = object.__new__(EnrichEngine)
    engine._indexing_mode = "steady"
    calls: list[tuple[str, int]] = []

    def _refresh() -> None:
        return None

    def _l2(limit: int) -> int:
        calls.append(("l2", limit))
        return limit

    def _l3(limit: int) -> int:
        calls.append(("l3", limit))
        return limit

    engine.refresh_indexing_mode = _refresh
    engine.process_enrich_jobs_l2 = _l2
    engine.process_enrich_jobs_l3 = _l3

    processed = engine.process_enrich_jobs_bootstrap(limit=7)

    assert processed == 7
    assert calls == [("l2", 4), ("l3", 3)]
