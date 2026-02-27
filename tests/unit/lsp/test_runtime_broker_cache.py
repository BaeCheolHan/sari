"""LspRuntimeBroker 캐시 동작을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path

from solidlsp.ls_config import Language

from sari.lsp.runtime_broker import LspRuntimeBroker


def test_discover_cached_java_executables_uses_ttl_cache(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / ".solidlsp" / "language_servers" / "static"
    java_bin = root / "A" / "bin" / "java"
    java_bin.parent.mkdir(parents=True, exist_ok=True)
    java_bin.write_text("", encoding="utf-8")

    broker = LspRuntimeBroker(java_min_major=17)
    broker._cached_static_java_bins_ttl_sec = 60.0

    calls = {"count": 0}

    def _fake_rglob(_self, _pattern: str):
        calls["count"] += 1
        return [java_bin]

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(Path, "rglob", _fake_rglob)

    first = broker._discover_cached_java_executables()
    second = broker._discover_cached_java_executables()

    assert first == [java_bin]
    assert second == [java_bin]
    assert calls["count"] == 1


def test_probe_java_major_uses_file_metadata_cache(monkeypatch, tmp_path: Path) -> None:
    java_bin = tmp_path / "bin" / "java"
    java_bin.parent.mkdir(parents=True, exist_ok=True)
    java_bin.write_text("", encoding="utf-8")

    broker = LspRuntimeBroker(java_min_major=17)

    calls = {"count": 0}

    class _FakeResult:
        stderr = 'openjdk version "21.0.7"\n'
        stdout = ""

    def _fake_run(*_args, **_kwargs):
        calls["count"] += 1
        return _FakeResult()

    monkeypatch.setattr("subprocess.run", _fake_run)

    major1 = broker._probe_java_major(java_bin)
    major2 = broker._probe_java_major(java_bin)

    assert major1 == 21
    assert major2 == 21
    assert calls["count"] == 1


def test_resolve_uses_repo_cache_hit_without_candidate_scan(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir(parents=True)
    java_bin = tmp_path / "jdk21" / "bin" / "java"
    java_bin.parent.mkdir(parents=True, exist_ok=True)
    java_bin.write_text("", encoding="utf-8")

    cache_file = tmp_path / "java_runtime_repo_cache.json"
    fingerprint = "fp:v1"
    cache_file.write_text(
        json.dumps(
            {
                "version": 1,
                "repos": {
                    str(repo_root.resolve()): {
                        "repo_fingerprint": fingerprint,
                        "major_map": {
                            "17": {
                                "selected_executable": str(java_bin),
                                "selected_major": 21,
                                "selected_source": "cache:test",
                                "java_stat": {
                                    "mtime": float(java_bin.stat().st_mtime),
                                    "size": int(java_bin.stat().st_size),
                                },
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("SARI_LSP_JAVA_RUNTIME_REPO_CACHE_PATH", str(cache_file))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.delenv("SARI_LSP_JAVA_BIN", raising=False)
    broker = LspRuntimeBroker(java_min_major=17)
    monkeypatch.setattr(broker, "_tool_path_overrides", lambda _language: {})
    monkeypatch.setattr(broker, "_compute_repo_fingerprint", lambda _repo_root: fingerprint)
    monkeypatch.setattr(broker, "_candidate_java_executables", lambda: (_ for _ in ()).throw(AssertionError("must not scan candidates")))

    context = broker.resolve(Language.JAVA, repo_root=str(repo_root))
    assert context.selected_executable == str(java_bin)
    assert context.selected_source == "persist:repo_java_runtime_cache"


def test_resolve_effective_required_uses_repo_required_major(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo-b"
    repo_root.mkdir(parents=True)
    (repo_root / ".java-version").write_text("21\n", encoding="utf-8")

    broker = LspRuntimeBroker(java_min_major=17)
    monkeypatch.setattr(broker, "_tool_path_overrides", lambda _language: {})
    monkeypatch.setattr(broker, "_candidate_java_executables", lambda: [("mock", Path("/jdk17/bin/java"))])
    monkeypatch.setattr(broker, "_probe_java_major", lambda _java: 17)

    context = broker.resolve(Language.JAVA, repo_root=str(repo_root))
    assert context.requirement is not None
    assert context.requirement.minimum_major == 21
    assert context.auto_provision_expected is False
    assert context.selected_major == 17
