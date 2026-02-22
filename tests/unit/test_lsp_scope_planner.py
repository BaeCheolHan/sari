from __future__ import annotations

from pathlib import Path
import threading
import os as _os

from solidlsp.ls_config import Language

from sari.services.collection.lsp_scope_planner import LspScopePlanner


def test_scope_planner_java_prefers_nearest_build_marker(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    module = repo_root / "services" / "api"
    src_dir = module / "src" / "main" / "java" / "com" / "example"
    src_dir.mkdir(parents=True)
    (repo_root / "settings.gradle").write_text("", encoding="utf-8")
    (module / "build.gradle").write_text("", encoding="utf-8")

    planner = LspScopePlanner()
    rel = "services/api/src/main/java/com/example/App.java"

    result = planner.resolve(
        workspace_repo_root=str(repo_root),
        relative_path=rel,
        language=Language.JAVA,
    )

    assert result.lsp_scope_root == str(module.resolve())
    assert result.strategy == "marker"
    assert result.marker_file == "build.gradle"


def test_scope_planner_java_falls_back_to_top_level_repo_without_marker(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    src_dir = repo_root / "nested" / "pkg"
    src_dir.mkdir(parents=True)

    planner = LspScopePlanner()
    result = planner.resolve(
        workspace_repo_root=str(repo_root),
        relative_path="nested/pkg/App.java",
        language=Language.JAVA,
    )

    assert result.lsp_scope_root == str(repo_root.resolve())
    assert result.strategy == "top_level_repo"
    assert result.marker_file is None


def test_scope_planner_ignores_node_modules_when_resolving_ts_marker(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    app_dir = repo_root / "apps" / "web"
    target_dir = app_dir / "src"
    target_dir.mkdir(parents=True)
    (app_dir / "package.json").write_text("{}", encoding="utf-8")
    bad_dir = repo_root / "node_modules" / "foo"
    bad_dir.mkdir(parents=True)
    (bad_dir / "package.json").write_text("{}", encoding="utf-8")

    planner = LspScopePlanner()
    result = planner.resolve(
        workspace_repo_root=str(repo_root),
        relative_path="apps/web/src/app.ts",
        language=Language.TYPESCRIPT,
    )

    assert result.lsp_scope_root == str(app_dir.resolve())
    assert result.strategy == "marker"
    assert result.marker_file == "package.json"


def test_scope_planner_invalidate_path_drops_cached_resolution(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    module = repo_root / "service"
    src_dir = module / "src"
    src_dir.mkdir(parents=True)
    marker = module / "pom.xml"
    marker.write_text("", encoding="utf-8")
    planner = LspScopePlanner()
    rel = "service/src/App.java"

    first = planner.resolve(
        workspace_repo_root=str(repo_root),
        relative_path=rel,
        language=Language.JAVA,
    )
    assert first.strategy == "marker"

    marker.unlink()
    second_without_invalidate = planner.resolve(
        workspace_repo_root=str(repo_root),
        relative_path=rel,
        language=Language.JAVA,
    )
    assert second_without_invalidate.strategy == "marker"

    removed = planner.invalidate_path(str(module))
    assert removed >= 1

    second = planner.resolve(
        workspace_repo_root=str(repo_root),
        relative_path=rel,
        language=Language.JAVA,
    )
    assert second.strategy == "top_level_repo"


def test_scope_planner_returns_fallback_index_building_when_marker_index_is_inflight(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    module = repo_root / "service"
    src_dir = module / "src"
    src_dir.mkdir(parents=True)
    (module / "pom.xml").write_text("", encoding="utf-8")

    started = threading.Event()
    release = threading.Event()

    class _BlockingPlanner(LspScopePlanner):
        def _build_marker_index(self, *, repo_path: Path, language: Language):  # type: ignore[override]
            started.set()
            release.wait(timeout=2.0)
            return super()._build_marker_index(repo_path=repo_path, language=language)

    planner = _BlockingPlanner()
    results: dict[str, object] = {}

    def _worker() -> None:
        results["owner"] = planner.resolve(
            workspace_repo_root=str(repo_root),
            relative_path="service/src/App.java",
            language=Language.JAVA,
        )

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    assert started.wait(timeout=1.0) is True

    follower = planner.resolve(
        workspace_repo_root=str(repo_root),
        relative_path="service/src/App.java",
        language=Language.JAVA,
    )
    assert follower.strategy == "FALLBACK_INDEX_BUILDING"
    assert follower.lsp_scope_root == str(repo_root.resolve())

    release.set()
    t.join(timeout=2.0)
    owner = results["owner"]
    assert owner.strategy == "marker"  # type: ignore[attr-defined]
    assert owner.lsp_scope_root == str(module.resolve())  # type: ignore[attr-defined]

    after = planner.resolve(
        workspace_repo_root=str(repo_root),
        relative_path="service/src/App.java",
        language=Language.JAVA,
    )
    assert after.strategy == "marker"


def test_scope_planner_scope_relative_path_conversion() -> None:
    planner = LspScopePlanner()
    converted = planner.to_scope_relative_path(
        workspace_relative_path="services/api/src/main/java/App.java",
        scope_candidate_root="services/api",
    )
    assert converted == "src/main/java/App.java"


def test_scope_planner_scope_relative_path_fallback_when_not_under_scope() -> None:
    planner = LspScopePlanner()
    converted = planner.to_scope_relative_path(
        workspace_relative_path="services/api/src/main/java/App.java",
        scope_candidate_root="other/module",
    )
    assert converted == "services/api/src/main/java/App.java"


def test_scope_planner_ts_prefers_nearest_app_marker_over_root_workspace_lockfile(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    app_dir = repo_root / "apps" / "web"
    src_dir = app_dir / "src"
    src_dir.mkdir(parents=True)
    (repo_root / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n", encoding="utf-8")
    (app_dir / "package.json").write_text("{}", encoding="utf-8")

    planner = LspScopePlanner(ts_markers=("tsconfig.json", "jsconfig.json", "package.json", "pnpm-workspace.yaml"))
    result = planner.resolve(
        workspace_repo_root=str(repo_root),
        relative_path="apps/web/src/main.ts",
        language=Language.TYPESCRIPT,
    )

    assert result.lsp_scope_root == str(app_dir.resolve())
    assert result.strategy == "marker"
    assert result.marker_file == "package.json"


def test_scope_planner_marker_index_prunes_ignored_dirs_during_walk(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path / "repo"
    app_dir = repo_root / "apps" / "web"
    src_dir = app_dir / "src"
    src_dir.mkdir(parents=True)
    (app_dir / "package.json").write_text("{}", encoding="utf-8")
    ignored_dir = repo_root / "node_modules" / "foo" / "bar"
    ignored_dir.mkdir(parents=True)
    (ignored_dir.parent / "package.json").write_text("{}", encoding="utf-8")

    visited_roots: list[str] = []
    real_walk = _os.walk

    def _recording_walk(*args, **kwargs):  # noqa: ANN001
        for root, dirs, files in real_walk(*args, **kwargs):
            visited_roots.append(str(root))
            yield root, dirs, files

    monkeypatch.setattr("sari.services.collection.lsp_scope_planner.os.walk", _recording_walk)

    planner = LspScopePlanner()
    index = planner._build_marker_index(repo_path=repo_root.resolve(), language=Language.TYPESCRIPT)  # type: ignore[attr-defined]

    assert str(app_dir.resolve()) in {str(p) for p in index.keys()}
    assert not any("node_modules/foo" in root for root in visited_roots)
