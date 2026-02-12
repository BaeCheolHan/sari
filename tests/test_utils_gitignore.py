from sari.core.utils.gitignore import GitignoreMatcher, load_gitignore, _GITIGNORE_CACHE


def test_gitignore_matcher_basic_rules():
    matcher = GitignoreMatcher(["*.log", "!keep.log", "build/"])
    assert matcher.is_ignored("x.log") is True
    assert matcher.is_ignored("keep.log") is False
    assert matcher.is_ignored("build", is_dir=True) is True


def test_gitignore_matcher_caches_repeated_queries(monkeypatch):
    matcher = GitignoreMatcher(["*.tmp"])
    calls = {"n": 0}
    original = matcher._match_rule

    def _wrapped(rule, rel_posix, is_dir):
        calls["n"] += 1
        return original(rule, rel_posix, is_dir)

    monkeypatch.setattr(matcher, "_match_rule", _wrapped)

    assert matcher.is_ignored("a.tmp") is True
    first = calls["n"]
    assert first > 0
    assert matcher.is_ignored("a.tmp") is True
    assert calls["n"] == first


def test_load_gitignore_uses_mtime_cache(tmp_path, monkeypatch):
    _GITIGNORE_CACHE.clear()
    root = tmp_path
    gi = root / ".gitignore"
    gi.write_text("*.log\n", encoding="utf-8")

    calls = {"n": 0}
    from pathlib import Path

    original = Path.read_text

    def _wrapped_read_text(self, *args, **kwargs):
        if self == gi:
            calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _wrapped_read_text)

    first = load_gitignore(root)
    second = load_gitignore(root)

    assert first == ["*.log"]
    assert second == ["*.log"]
    assert calls["n"] == 1
