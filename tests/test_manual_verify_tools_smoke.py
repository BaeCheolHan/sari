from tools.manual.verify_tools_smoke import _filter_test_cases_by_registry


def test_filter_test_cases_by_registry_skips_unavailable_tools():
    test_cases = [
        ("status", {}),
        ("search_symbols", {"query": "x"}),
        ("search", {"query": "x"}),
        ("repo_candidates", {"query": "x"}),
    ]
    available = {"status", "search"}

    filtered, skipped = _filter_test_cases_by_registry(test_cases, available)

    assert [name for name, _ in filtered] == ["status", "search"]
    assert skipped == ["search_symbols", "repo_candidates"]
