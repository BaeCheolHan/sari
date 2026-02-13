import json
from types import SimpleNamespace

import pytest

from sari.core.async_http_server import AsyncHttpServer
from sari.core.http_server import Handler


def _make_sync_handler():
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/ws"
    handler.server_host = "127.0.0.1"
    handler.server_port = 47777
    handler.server_version = "0.0.test"
    handler.start_time = 0
    handler.db = SimpleNamespace(
        get_repo_stats=lambda root_ids=None: {},
        get_roots=lambda: [],
        db_path="/tmp/index.db",
        writer=SimpleNamespace(qsize=lambda: 0),
    )
    handler.indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, scan_finished_ts=1, indexed_files=2, scanned_files=3, errors=0),
        get_runtime_status=lambda: {"status_source": "indexer_status"},
        get_performance_metrics=lambda: {},
        get_queue_depths=lambda: {},
    )
    handler.root_ids = []
    return handler


@pytest.mark.asyncio
async def test_status_contract_common_fields_sync_async(monkeypatch):
    monkeypatch.setattr("sari.core.http_server.detect_orphan_daemons", lambda: [])
    monkeypatch.setattr(
        "sari.core.http_server.get_system_metrics",
        lambda: {"process_cpu_percent": 0, "memory_percent": 0},
    )
    monkeypatch.setattr("sari.core.async_http_server.detect_orphan_daemons", lambda: [])

    sync_handler = _make_sync_handler()
    sync_payload = Handler._handle_get(sync_handler, "/status", {})

    async_db = SimpleNamespace(get_repo_stats=lambda root_ids=None: {}, get_roots=lambda: [], fts_enabled=True)
    async_indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, scan_finished_ts=1, indexed_files=2, scanned_files=3, errors=0),
        get_runtime_status=lambda: {"status_source": "indexer_status"},
        get_last_commit_ts=lambda: 0,
        get_performance_metrics=lambda: {},
        get_queue_depths=lambda: {},
    )
    async_server = AsyncHttpServer(async_db, async_indexer, workspace_root="/tmp/ws")
    async_resp = await async_server.status(SimpleNamespace())
    async_payload = json.loads(async_resp.body.decode("utf-8"))

    common_required = {
        "ok",
        "host",
        "port",
        "version",
        "index_ready",
        "last_scan_ts",
        "scanned_files",
        "indexed_files",
        "symbols_extracted",
        "total_files_db",
        "errors",
        "status_source",
        "orphan_daemon_count",
        "orphan_daemon_warnings",
        "signals_disabled",
        "shutdown_intent",
        "suicide_state",
        "active_leases_count",
        "leases",
        "last_reap_at",
        "reaper_last_run_at",
        "no_client_since",
        "grace_remaining",
        "grace_remaining_ms",
        "shutdown_once_set",
        "last_event_ts",
        "event_queue_depth",
        "last_shutdown_reason",
        "shutdown_reason",
        "workers_alive",
        "performance",
        "queue_depths",
        "repo_stats",
        "roots",
        "system_metrics",
        "status_warning_counts",
        "warning_counts",
        "warnings_recent",
        "deployment",
    }
    assert common_required.issubset(set(sync_payload.keys()))
    assert common_required.issubset(set(async_payload.keys()))


def test_sync_status_contract_specific_fields(monkeypatch):
    monkeypatch.setattr("sari.core.http_server.detect_orphan_daemons", lambda: [])
    monkeypatch.setattr(
        "sari.core.http_server.get_system_metrics",
        lambda: {"process_cpu_percent": 0, "memory_percent": 0},
    )
    handler = _make_sync_handler()
    payload = Handler._handle_get(handler, "/status", {})
    assert "workspace_root" in payload
    assert "workspaces" in payload
    assert "registry_resolve_failed" in payload
    assert "deployment" in payload
