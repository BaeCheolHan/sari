"""LSP tool data 저장소의 scope_root 조회 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.schema import connect, init_schema


def test_lsp_tool_data_repository_search_symbols_supports_scope_root(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace"
    module_root = "/workspace/mod-a"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', '/workspace/mod-a/src/main.py', 'mod-a',
                1, 10, 'h1', 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {"repo_root": module_root, "scope_repo_root": scope_root},
        )
        conn.commit()

    repo.replace_symbols(
        repo_root=module_root,
        relative_path="src/main.py",
        content_hash="h1",
        symbols=[{"name": "AuthService", "kind": "Class", "line": 10, "end_line": 40}],
        created_at="2026-02-25T00:00:00+00:00",
    )

    rows = repo.search_symbols(repo_root=scope_root, query="Auth", limit=10)
    assert len(rows) == 1
    assert rows[0].repo == module_root
    assert rows[0].name == "AuthService"


def test_lsp_tool_data_repository_callers_and_health_support_scope_root(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace"
    module_root = "/workspace/mod-a"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', '/workspace/mod-a/src/main.py', 'mod-a',
                1, 10, 'h1', 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {"repo_root": module_root, "scope_repo_root": scope_root},
        )
        conn.commit()

    repo.replace_relations(
        repo_root=module_root,
        relative_path="src/main.py",
        content_hash="h1",
        relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 21}],
        created_at="2026-02-25T00:00:00+00:00",
    )

    callers = repo.find_callers(repo_root=scope_root, symbol_name="AuthService.login", limit=20)
    assert len(callers) == 1
    assert callers[0].repo == module_root
    assert callers[0].from_symbol == "AuthController.login"

    assert repo.count_distinct_callers(repo_root=scope_root, symbol_name="AuthService.login") == 1
    health = repo.get_repo_call_graph_health(repo_root=scope_root)
    assert health["relation_count"] >= 1


def test_lsp_tool_data_repository_count_distinct_callers_counts_per_repo_path_pair(tmp_path: Path) -> None:
    """scope 집계 시 동일 relative_path라도 module repo가 다르면 caller를 별도로 집계해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace"
    module_a = "/workspace/mod-a"
    module_b = "/workspace/mod-b"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', :absolute_path, 'mod',
                1, 10, :content_hash, 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": module_a,
                "scope_repo_root": scope_root,
                "absolute_path": "/workspace/mod-a/src/main.py",
                "content_hash": "ha",
            },
        )
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', :absolute_path, 'mod',
                1, 10, :content_hash, 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": module_b,
                "scope_repo_root": scope_root,
                "absolute_path": "/workspace/mod-b/src/main.py",
                "content_hash": "hb",
            },
        )
        conn.commit()

    repo.replace_relations(
        repo_root=module_a,
        relative_path="src/main.py",
        content_hash="ha",
        relations=[{"from_symbol": "A.call", "to_symbol": "Common.target", "line": 10}],
        created_at="2026-02-25T00:00:00+00:00",
    )
    repo.replace_relations(
        repo_root=module_b,
        relative_path="src/main.py",
        content_hash="hb",
        relations=[{"from_symbol": "B.call", "to_symbol": "Common.target", "line": 20}],
        created_at="2026-02-25T00:00:00+00:00",
    )

    assert repo.count_distinct_callers(repo_root=scope_root, symbol_name="Common.target") == 2


def test_lsp_tool_data_repository_search_symbols_dedupes_same_absolute_file_across_repo_roots(tmp_path: Path) -> None:
    """동일 절대 파일이 서로 다른 repo_root로 저장돼도 search_symbols는 중복을 제거해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace/proj"
    nested_root = "/workspace/proj/src"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, :relative_path, :absolute_path, 'proj-src',
                1, 10, 'h1', 0, '2026-03-03T00:00:00+00:00', '2026-03-03T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": nested_root,
                "scope_repo_root": scope_root,
                "relative_path": "sari/mcp/proxy.py",
                "absolute_path": "/workspace/proj/src/sari/mcp/proxy.py",
            },
        )
        conn.commit()

    repo.replace_symbols(
        repo_root=scope_root,
        relative_path="src/sari/mcp/proxy.py",
        content_hash="h1",
        symbols=[
            {
                "name": "run_stdio_proxy",
                "kind": "function",
                "line": 157,
                "end_line": 240,
                "symbol_key": "scope::run_stdio_proxy",
            }
        ],
        created_at="2026-03-03T00:00:00+00:00",
    )
    repo.replace_symbols(
        repo_root=nested_root,
        relative_path="sari/mcp/proxy.py",
        content_hash="h1",
        symbols=[
            {
                "name": "run_stdio_proxy",
                "kind": "function",
                "line": 157,
                "end_line": 240,
                "symbol_key": "nested::run_stdio_proxy",
            }
        ],
        created_at="2026-03-03T00:00:00+00:00",
    )

    rows = repo.search_symbols(repo_root=scope_root, query="run_stdio_proxy", limit=10)
    assert len(rows) == 1
    assert rows[0].repo == scope_root
    assert rows[0].relative_path == "src/sari/mcp/proxy.py"


def test_lsp_tool_data_repository_search_symbols_applies_limit_after_dedupe(tmp_path: Path) -> None:
    """중복 제거가 먼저 적용되어야 limit 창에서 유효한 고유 결과가 누락되지 않는다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace/proj"
    nested_root = "/workspace/proj/src"
    module_root = "/workspace/proj/mod"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES
                ('', :nested_root, :scope_root, 'dup.py', '/workspace/proj/src/dup.py', 'proj-src', 1, 10, 'h1', 0, '2026-03-03T00:00:00+00:00', '2026-03-03T00:00:00+00:00', 'DONE'),
                ('', :module_root, :scope_root, 'z.py', '/workspace/proj/mod/z.py', 'proj-mod', 1, 10, 'h2', 0, '2026-03-03T00:00:00+00:00', '2026-03-03T00:00:00+00:00', 'DONE')
            """,
            {
                "scope_root": scope_root,
                "nested_root": nested_root,
                "module_root": module_root,
            },
        )
        conn.commit()

    repo.replace_symbols(
        repo_root=scope_root,
        relative_path="src/dup.py",
        content_hash="h1",
        symbols=[{"name": "TargetDup", "kind": "function", "line": 1, "end_line": 3}],
        created_at="2026-03-03T00:00:00+00:00",
    )
    repo.replace_symbols(
        repo_root=nested_root,
        relative_path="dup.py",
        content_hash="h1",
        symbols=[{"name": "TargetDup", "kind": "function", "line": 1, "end_line": 3}],
        created_at="2026-03-03T00:00:00+00:00",
    )
    repo.replace_symbols(
        repo_root=module_root,
        relative_path="z.py",
        content_hash="h2",
        symbols=[{"name": "TargetUnique", "kind": "function", "line": 2, "end_line": 4}],
        created_at="2026-03-03T00:00:00+00:00",
    )

    rows = repo.search_symbols(repo_root=scope_root, query="Target", limit=2)
    assert len(rows) == 2
    assert {item.name for item in rows} == {"TargetDup", "TargetUnique"}


def test_lsp_tool_data_repository_find_implementations_applies_limit_after_dedupe(tmp_path: Path) -> None:
    """find_implementations도 dedupe 이후 limit을 적용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace/proj"
    nested_root = "/workspace/proj/src"
    module_root = "/workspace/proj/mod"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES
                ('', :nested_root, :scope_root, 'dup.py', '/workspace/proj/src/dup.py', 'proj-src', 1, 10, 'h1', 0, '2026-03-03T00:00:00+00:00', '2026-03-03T00:00:00+00:00', 'DONE'),
                ('', :module_root, :scope_root, 'z.py', '/workspace/proj/mod/z.py', 'proj-mod', 1, 10, 'h2', 0, '2026-03-03T00:00:00+00:00', '2026-03-03T00:00:00+00:00', 'DONE')
            """,
            {
                "scope_root": scope_root,
                "nested_root": nested_root,
                "module_root": module_root,
            },
        )
        conn.commit()

    repo.replace_symbols(
        repo_root=scope_root,
        relative_path="src/dup.py",
        content_hash="h1",
        symbols=[{"name": "TargetDupImpl", "kind": "function", "line": 1, "end_line": 3}],
        created_at="2026-03-03T00:00:00+00:00",
    )
    repo.replace_symbols(
        repo_root=nested_root,
        relative_path="dup.py",
        content_hash="h1",
        symbols=[{"name": "TargetDupImpl", "kind": "function", "line": 1, "end_line": 3}],
        created_at="2026-03-03T00:00:00+00:00",
    )
    repo.replace_symbols(
        repo_root=module_root,
        relative_path="z.py",
        content_hash="h2",
        symbols=[{"name": "TargetUniqueImpl", "kind": "function", "line": 2, "end_line": 4}],
        created_at="2026-03-03T00:00:00+00:00",
    )

    rows = repo.find_implementations(repo_root=scope_root, symbol_name="Target", limit=2)
    assert len(rows) == 2
    assert {item.name for item in rows} == {"TargetDupImpl", "TargetUniqueImpl"}


def test_lsp_tool_data_repository_search_symbols_keeps_repo_preference_across_batches(tmp_path: Path) -> None:
    """중복 키의 preferred repo 항목이 후속 배치에 있어도 최종 선택에 반영되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace/proj"
    nested_root = "/workspace/proj/src"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES
                ('', :nested_root, :scope_root, 'dup.py', '/workspace/proj/src/dup.py', 'proj-src', 1, 10, 'h1', 0, '2026-03-03T00:00:00+00:00', '2026-03-03T00:00:00+00:00', 'DONE'),
                ('', :scope_root, :scope_root, 'src/dup.py', '/workspace/proj/src/dup.py', 'proj', 1, 10, 'h1', 0, '2026-03-03T00:00:00+00:00', '2026-03-03T00:00:00+00:00', 'DONE')
            """,
            {
                "scope_root": scope_root,
                "nested_root": nested_root,
            },
        )
        conn.commit()

    repo.replace_symbols(
        repo_root=nested_root,
        relative_path="dup.py",
        content_hash="h1",
        symbols=[{"name": "TargetPreferred", "kind": "function", "line": 1, "end_line": 2}],
        created_at="2026-03-03T00:00:00+00:00",
    )

    # 배치 경계를 넘기기 위해 첫 배치(64행) 대부분을 filler로 채운다.
    for i in range(64):
        rel = f"m{i:02d}.py"
        repo.replace_symbols(
            repo_root=scope_root,
            relative_path=rel,
            content_hash=f"h{i+10}",
            symbols=[{"name": f"TargetFill{i:02d}", "kind": "function", "line": 1, "end_line": 2}],
            created_at="2026-03-03T00:00:00+00:00",
        )

    repo.replace_symbols(
        repo_root=scope_root,
        relative_path="src/dup.py",
        content_hash="h1",
        symbols=[{"name": "TargetPreferred", "kind": "function", "line": 1, "end_line": 2}],
        created_at="2026-03-03T00:00:00+00:00",
    )

    rows = repo.search_symbols(repo_root=scope_root, query="TargetPreferred", limit=1)
    assert len(rows) == 1
    assert rows[0].repo == scope_root
    assert rows[0].relative_path == "src/dup.py"


def test_lsp_tool_data_repository_search_symbols_limit_head_matches_global_order_across_batches(tmp_path: Path) -> None:
    """배치 경계가 있어도 limit=1 결과는 전체 정렬의 첫 행과 일치해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace/proj"
    symbols: list[dict[str, object]] = []
    for i in range(80, 0, -1):
        symbols.append({"name": f"N{i:03d}", "kind": "function", "line": 1, "end_line": 2})
    symbols.append({"name": "A000", "kind": "function", "line": 1, "end_line": 2})

    repo.replace_symbols(
        repo_root=scope_root,
        relative_path="a.py",
        content_hash="h1",
        symbols=symbols,
        created_at="2026-03-03T00:00:00+00:00",
    )

    first = repo.search_symbols(repo_root=scope_root, query="", limit=1)
    all_rows = repo.search_symbols(repo_root=scope_root, query="", limit=500)
    assert len(first) == 1
    assert len(all_rows) >= 1
    assert first[0].name == all_rows[0].name


def test_lsp_tool_data_repository_search_symbols_does_not_break_before_later_higher_priority_rows(tmp_path: Path) -> None:
    """requested-root 승격 후에도 더 앞서는 later row가 있으면 계속 스캔해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace/proj"
    with connect(db_path) as conn:
        for i in range(70):
            alias_root = f"/workspace/proj/src/a{i}/.."
            conn.execute(
                """
                INSERT INTO collected_files_l1(
                    repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                    mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
                ) VALUES(
                    '', :repo_root, :scope_repo_root, 'dup.py', '/workspace/proj/src/dup.py', 'proj-alias',
                    1, 10, 'h1', 0, '2026-03-03T00:00:00+00:00', '2026-03-03T00:00:00+00:00', 'DONE'
                )
                """,
                {
                    "repo_root": alias_root,
                    "scope_repo_root": scope_root,
                },
            )
        conn.commit()

    repo.replace_symbols(
        repo_root=scope_root,
        relative_path="src/dup.py",
        content_hash="h1",
        symbols=[{"name": "Target", "kind": "function", "line": 1, "end_line": 2}],
        created_at="2026-03-03T00:00:00+00:00",
    )
    for i in range(70):
        alias_root = f"/workspace/proj/src/a{i}/.."
        repo.replace_symbols(
            repo_root=alias_root,
            relative_path="dup.py",
            content_hash="h1",
            symbols=[{"name": "Target", "kind": "function", "line": 1, "end_line": 2}],
            created_at="2026-03-03T00:00:00+00:00",
        )
    repo.replace_symbols(
        repo_root=scope_root,
        relative_path="m.py",
        content_hash="h2",
        symbols=[{"name": "TargetB", "kind": "function", "line": 1, "end_line": 2}],
        created_at="2026-03-03T00:00:00+00:00",
    )

    rows = repo.search_symbols(repo_root=scope_root, query="Target", limit=1)
    assert len(rows) == 1
    assert rows[0].relative_path == "m.py"
    assert rows[0].name == "TargetB"
