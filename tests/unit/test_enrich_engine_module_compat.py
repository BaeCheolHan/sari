from __future__ import annotations

import sari.services.collection.enrich_engine as enrich_engine_module
from sari.services.collection.enrich_engine import EnrichEngine


def test_enrich_engine_module_keeps_l3_orchestrator_symbol_for_test_compat() -> None:
    # batch17 회귀 테스트는 enrich_engine 모듈 경유 심볼을 사용한다.
    assert hasattr(enrich_engine_module, "L3Orchestrator")


def test_enrich_engine_module_exports_engine_class() -> None:
    assert EnrichEngine is enrich_engine_module.EnrichEngine
