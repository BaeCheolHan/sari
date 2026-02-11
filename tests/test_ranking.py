from sari.core.ranking import snippet_around


def test_snippet_around_tolerates_non_text_content():
    snippet = snippet_around(12345, [], 3, highlight=False)
    assert snippet == "L1: 12345"
