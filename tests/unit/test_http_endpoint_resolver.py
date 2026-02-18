"""HTTP endpoint resolver 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import DaemonRegistryEntryDTO
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.schema import init_schema
from sari.http.endpoint_resolver import resolve_http_endpoint


def test_http_endpoint_resolver_uses_registry_priority(tmp_path: Path) -> None:
    """HTTP endpoint resolver는 daemon registry 최신 ACTIVE 엔트리를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repository = DaemonRegistryRepository(db_path)
    repository.upsert(
        DaemonRegistryEntryDTO(
            daemon_id="d-http-1",
            host="127.0.0.1",
            port=48801,
            pid=401,
            workspace_root="/repo/http",
            protocol="http",
            started_at="2026-02-18T00:00:00+00:00",
            last_seen_at="2026-02-18T00:00:01+00:00",
            is_draining=False,
        )
    )

    resolved = resolve_http_endpoint(db_path=db_path, workspace_root="/repo/http")
    assert resolved.host == "127.0.0.1"
    assert resolved.port == 48801
    assert resolved.reason == "daemon:registry_active"
