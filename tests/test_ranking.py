from sari.core.ranking import count_matches, glob_to_like, match_path_pattern, snippet_around


def test_snippet_around_tolerates_non_text_content():
    snippet = snippet_around(12345, [], 3, highlight=False)
    assert snippet == "L1: 12345"


def test_snippet_around_avoids_nested_highlight_tags():
    snippet = snippet_around("search_engine", ["search", "search_engine"], 1, highlight=True)
    assert ">>>search_engine<<<" in snippet
    assert ">>>search<<<_engine" not in snippet


def test_count_matches_literal_handles_unicode_normalization_case_insensitive():
    content = "Cafe\u0301 and CAFE\u0301"
    assert count_matches(content, "café", use_regex=False, case_sensitive=False) == 2


def test_snippet_around_handles_max_lines_greater_than_file_lines():
    snippet = snippet_around("one\ntwo", ["one"], 10, highlight=True)
    assert "L1: >>>one<<<" in snippet


def test_glob_to_like_exact_path_without_wildcard_is_not_contains_match():
    assert glob_to_like("src/main.py") == "src/main.py"


def test_match_path_pattern_supports_db_style_path_with_root_prefix():
    assert match_path_pattern("rid/src/app/main.py", "src/app/main.py", "src/**") is True
    assert match_path_pattern("rid/src/app/main.py", "src/app/main.py", "rid/src/**") is True


def test_match_path_pattern_can_match_from_rel_path_without_first_segment():
    assert match_path_pattern("rid/src/.idea/workspace.xml", "src/.idea/workspace.xml", ".idea/**") is True
    assert match_path_pattern("rid/libs/.venv/site.py", "libs/.venv/site.py", ".venv/**") is True
