from __future__ import annotations

from sari.services.collection.enrich_jobs_processor import EnrichJobsProcessor


def test_python_symbol_fallback_extracts_async_and_regular_defs() -> None:
    processor = object.__new__(EnrichJobsProcessor)
    symbols = processor._fallback_symbols_for_empty_extract(  # type: ignore[attr-defined]
        relative_path="src/sari/http/meta_endpoints.py",
        content_text=(
            "class Meta:\n"
            "    pass\n\n"
            "async def status_endpoint(request):\n"
            "    return None\n\n"
            "def health_endpoint(request):\n"
            "    return None\n"
        ),
    )
    names = {str(item["name"]) for item in symbols}
    assert "Meta" in names
    assert "status_endpoint" in names
    assert "health_endpoint" in names


def test_python_symbol_fallback_ignores_non_python_files() -> None:
    processor = object.__new__(EnrichJobsProcessor)
    symbols = processor._fallback_symbols_for_empty_extract(  # type: ignore[attr-defined]
        relative_path="src/sari/http/meta_endpoints.ts",
        content_text="function status_endpoint() {}",
    )
    assert symbols == []
