"""L3/L4/L5 분리 tool_data 저장소 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path
import hashlib

from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.db.schema import init_schema
from sari.db.schema import connect


def test_tool_data_layer_repository_roundtrip_by_content_hash(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-23T12:00:00+00:00"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', '/repo', 'src/a.py', '/repo/src/a.py', 'repo',
                1, 10, 'h1', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"now_iso": now_iso},
        )
        conn.commit()

    repo.upsert_l3_symbols(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        symbols=[{"name": "A"}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at=now_iso,
    )
    repo.upsert_l4_normalized_symbols(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        normalized={"top": ["A"]},
        confidence=0.9,
        ambiguity=0.1,
        coverage=0.95,
        updated_at=now_iso,
    )
    repo.upsert_l5_semantics(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        reason_code="L5_REASON_UNRESOLVED_SYMBOL",
        semantics={"edges": 3},
        updated_at=now_iso,
    )

    snapshot = repo.load_effective_snapshot(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
    )
    assert snapshot["l3"] is not None
    assert snapshot["l4"] is not None
    assert isinstance(snapshot["l5"], list)
    assert snapshot["l3"]["symbols"][0]["name"] == "A"
    assert snapshot["l5"][0]["reason_code"] == "L5_REASON_UNRESOLVED_SYMBOL"


def test_tool_data_layer_repository_batch_upserts_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-23T12:00:00+00:00"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', '/repo', 'src/a.py', '/repo/src/a.py', 'repo',
                1, 10, 'h1', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"now_iso": now_iso},
        )
        conn.commit()

    repo.upsert_l3_symbols_many(
        [
            {
                "workspace_id": "ws-a",
                "repo_root": "/repo",
                "relative_path": "src/a.py",
                "content_hash": "h1",
                "symbols": [{"name": "A", "kind": "class", "line": 1, "end_line": 1}],
                "degraded": False,
                "l3_skipped_large_file": False,
                "updated_at": now_iso,
            }
        ]
    )
    repo.upsert_l4_normalized_symbols_many(
        [
            {
                "workspace_id": "ws-a",
                "repo_root": "/repo",
                "relative_path": "src/a.py",
                "content_hash": "h1",
                "normalized": {"top": ["A"]},
                "confidence": 0.95,
                "ambiguity": 0.05,
                "coverage": 0.9,
                "updated_at": now_iso,
            }
        ]
    )
    repo.upsert_l5_semantics_many(
        [
            {
                "workspace_id": "ws-a",
                "repo_root": "/repo",
                "relative_path": "src/a.py",
                "content_hash": "h1",
                "reason_code": "L5_REASON_GOLDENSET_COVERAGE",
                "semantics": {"edges": 4},
                "updated_at": now_iso,
            }
        ]
    )

    snapshot = repo.load_effective_snapshot(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
    )
    assert snapshot["l3"]["symbols"][0]["name"] == "A"
    assert snapshot["l5"][0]["reason_code"] == "L5_REASON_GOLDENSET_COVERAGE"


def test_tool_data_layer_repository_drops_stale_l5_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-23T12:00:00+00:00"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', '/repo', 'src/a.py', '/repo/src/a.py', 'repo',
                1, 10, 'old-hash', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"now_iso": now_iso},
        )
        conn.commit()

    repo.upsert_l5_semantics(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="old-hash",
        reason_code="L5_REASON_GOLDENSET_COVERAGE",
        semantics={"edges": 1},
        updated_at=now_iso,
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE collected_files_l1
            SET content_hash = 'new-hash', updated_at = :now_iso
            WHERE repo_root = '/repo' AND relative_path = 'src/a.py'
            """,
            {"now_iso": now_iso},
        )
        conn.commit()
    repo.upsert_l5_semantics(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="new-hash",
        reason_code="L5_REASON_GOLDENSET_COVERAGE",
        semantics={"edges": 2},
        updated_at=now_iso,
    )

    deleted = repo.drop_stale_l5_semantics(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        active_content_hash="new-hash",
    )
    assert deleted == 1

    old_snapshot = repo.load_effective_snapshot(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="old-hash",
    )
    new_snapshot = repo.load_effective_snapshot(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="new-hash",
    )
    assert old_snapshot["l5"] == []
    assert len(new_snapshot["l5"]) == 1


def test_tool_data_layer_repository_search_l3_symbols_includes_l4_l5(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-23T12:00:00+00:00"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', '/repo', 'src/a.py', '/repo/src/a.py', 'repo',
                1, 10, 'h1', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"now_iso": now_iso},
        )
        conn.commit()

    repo.upsert_l3_symbols(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        symbols=[{"name": "Alpha", "kind": "function", "line": 1, "end_line": 2}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at=now_iso,
    )
    repo.upsert_l4_normalized_symbols(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        normalized={"outline": ["Alpha"]},
        confidence=0.9,
        ambiguity=0.1,
        coverage=0.95,
        updated_at=now_iso,
    )
    repo.upsert_l5_semantics(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        reason_code="L5_REASON_UNRESOLVED_SYMBOL",
        semantics={"edges": 2},
        updated_at=now_iso,
    )

    rows = repo.search_l3_symbols(
        workspace_id="ws-a",
        repo_root="/repo",
        query="Alpha",
        limit=10,
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Alpha"
    assert isinstance(rows[0]["l4"], dict)
    assert rows[0]["l4"]["normalized"]["outline"] == ["Alpha"]
    assert isinstance(rows[0]["l5"], list)
    assert rows[0]["l5"][0]["reason_code"] == "L5_REASON_UNRESOLVED_SYMBOL"


def test_tool_data_layer_repository_snapshot_ignores_stale_hash_vs_active_file(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-23T12:00:00+00:00"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', '/repo', 'src/a.py', '/repo/src/a.py', 'repo',
                1, 10, 'new-hash', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"now_iso": now_iso},
        )
        conn.commit()
    repo.upsert_l3_symbols(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="old-hash",
        symbols=[{"name": "Old"}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at=now_iso,
    )

    snapshot = repo.load_effective_snapshot(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="old-hash",
    )
    assert snapshot["l3"] is None
    assert snapshot["l4"] is None
    assert snapshot["l5"] == []


def test_tool_data_layer_repository_upsert_l5_skips_stale_hash_vs_active_file(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-23T12:00:00+00:00"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', '/repo', 'src/a.py', '/repo/src/a.py', 'repo',
                1, 10, 'new-hash', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"now_iso": now_iso},
        )
        conn.commit()

    repo.upsert_l5_semantics(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="old-hash",
        reason_code="L5_REASON_UNRESOLVED_SYMBOL",
        semantics={"edges": 3},
        updated_at=now_iso,
    )

    snapshot = repo.load_effective_snapshot(
        workspace_id="ws-a",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="new-hash",
    )
    assert snapshot["l5"] == []


def test_tool_data_layer_repository_load_supports_legacy_workspace_hash_key(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-23T12:00:00+00:00"
    repo_root = "/repo"
    relative_path = "src/a.py"
    content_hash = "h1"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :relative_path, '/repo/src/a.py', 'repo',
                1, 10, :content_hash, 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {
                "repo_root": repo_root,
                "relative_path": relative_path,
                "content_hash": content_hash,
                "now_iso": now_iso,
            },
        )
        conn.commit()
    legacy_workspace_id = hashlib.sha1(repo_root.encode("utf-8")).hexdigest()
    repo.upsert_l4_normalized_symbols(
        workspace_id=legacy_workspace_id,
        repo_root=repo_root,
        relative_path=relative_path,
        content_hash=content_hash,
        normalized={"outline": ["Alpha"]},
        confidence=0.9,
        ambiguity=0.1,
        coverage=0.95,
        updated_at=now_iso,
    )

    snapshot = repo.load_effective_snapshot(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path=relative_path,
        content_hash=content_hash,
    )
    assert isinstance(snapshot["l4"], dict)
    assert snapshot["l4"]["normalized"]["outline"] == ["Alpha"]


def test_tool_data_layer_repository_load_effective_snapshot_supports_scope_root_unique_match(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-25T12:00:00+00:00"
    scope_root = "/workspace"
    module_root = "/workspace/mod-a"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/a.py', '/workspace/mod-a/src/a.py', 'repo',
                1, 10, 'h1', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"repo_root": module_root, "scope_repo_root": scope_root, "now_iso": now_iso},
        )
        conn.commit()

    repo.upsert_l3_symbols(
        workspace_id=scope_root,
        repo_root=module_root,
        scope_repo_root=scope_root,
        relative_path="src/a.py",
        content_hash="h1",
        symbols=[{"name": "A"}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at=now_iso,
    )

    snapshot = repo.load_effective_snapshot(
        workspace_id=scope_root,
        repo_root=scope_root,
        relative_path="src/a.py",
        content_hash="h1",
    )
    assert isinstance(snapshot["l3"], dict)
    assert snapshot["l3"]["symbols"][0]["name"] == "A"


def test_tool_data_layer_repository_search_l3_symbols_supports_scope_root(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-25T12:00:00+00:00"
    scope_root = "/workspace"
    module_root = "/workspace/mod-a"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/a.py', '/workspace/mod-a/src/a.py', 'repo',
                1, 10, 'h1', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"repo_root": module_root, "scope_repo_root": scope_root, "now_iso": now_iso},
        )
        conn.commit()

    repo.upsert_l3_symbols(
        workspace_id=scope_root,
        repo_root=module_root,
        scope_repo_root=scope_root,
        relative_path="src/a.py",
        content_hash="h1",
        symbols=[{"name": "Alpha", "kind": "function", "line": 1, "end_line": 1}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at=now_iso,
    )

    rows = repo.search_l3_symbols(
        workspace_id=scope_root,
        repo_root=scope_root,
        query="Alpha",
        limit=5,
    )
    assert len(rows) == 1
    assert rows[0]["repo"] == module_root


def test_tool_data_layer_repository_search_scope_root_falls_back_when_workspace_id_is_module(tmp_path: Path) -> None:
    """scope 조회 시 writer workspace_id가 module root여도 L3 검색이 비지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-25T12:00:00+00:00"
    scope_root = "/workspace"
    module_root = "/workspace/mod-a"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/a.py', '/workspace/mod-a/src/a.py', 'repo',
                1, 10, 'h1', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"repo_root": module_root, "scope_repo_root": scope_root, "now_iso": now_iso},
        )
        conn.commit()

    # 실제 writer 경로처럼 workspace_id=module_root, scope_repo_root 누락(=repo_root로 저장) 케이스를 재현한다.
    repo.upsert_l3_symbols(
        workspace_id=module_root,
        repo_root=module_root,
        relative_path="src/a.py",
        content_hash="h1",
        symbols=[{"name": "Alpha", "kind": "function", "line": 1, "end_line": 1}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at=now_iso,
    )

    rows = repo.search_l3_symbols(
        workspace_id=scope_root,
        repo_root=scope_root,
        query="Alpha",
        limit=5,
    )
    assert len(rows) == 1
    assert rows[0]["repo"] == module_root


def test_tool_data_layer_repository_load_snapshot_scope_root_uses_effective_workspace_candidates(tmp_path: Path) -> None:
    """scope 요청에서 effective repo_root가 module로 해석되면 module workspace_id 후보도 조회에 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolDataLayerRepository(db_path)
    now_iso = "2026-02-25T12:00:00+00:00"
    scope_root = "/workspace"
    module_root = "/workspace/mod-a"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/a.py', '/workspace/mod-a/src/a.py', 'repo',
                1, 10, 'h1', 0, :now_iso, :now_iso, 'READY'
            )
            """,
            {"repo_root": module_root, "scope_repo_root": scope_root, "now_iso": now_iso},
        )
        conn.commit()

    repo.upsert_l3_symbols(
        workspace_id=module_root,
        repo_root=module_root,
        relative_path="src/a.py",
        content_hash="h1",
        symbols=[{"name": "Alpha", "kind": "function", "line": 1, "end_line": 1}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at=now_iso,
    )
    repo.upsert_l4_normalized_symbols(
        workspace_id=module_root,
        repo_root=module_root,
        relative_path="src/a.py",
        content_hash="h1",
        normalized={"outline": ["Alpha"]},
        confidence=0.9,
        ambiguity=0.1,
        coverage=0.95,
        updated_at=now_iso,
    )
    repo.upsert_l5_semantics(
        workspace_id=module_root,
        repo_root=module_root,
        relative_path="src/a.py",
        content_hash="h1",
        reason_code="L5_REASON_UNRESOLVED_SYMBOL",
        semantics={"edges": 2},
        updated_at=now_iso,
    )

    snapshot = repo.load_effective_snapshot(
        workspace_id=scope_root,
        repo_root=scope_root,
        relative_path="src/a.py",
        content_hash="h1",
    )
    assert isinstance(snapshot["l3"], dict)
    assert isinstance(snapshot["l4"], dict)
    assert isinstance(snapshot["l5"], list)
    assert snapshot["l3"]["symbols"][0]["name"] == "Alpha"
    assert snapshot["l4"]["normalized"]["outline"] == ["Alpha"]
    assert snapshot["l5"][0]["reason_code"] == "L5_REASON_UNRESOLVED_SYMBOL"
