"""데몬 엔드포인트 해석 유틸을 제공한다."""

from __future__ import annotations

import os
from pathlib import Path

from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.runtime_repository import RuntimeRepository


def resolve_daemon_address(db_path: Path, workspace_root: str | None = None) -> tuple[str, int]:
    """레지스트리 우선으로 데몬 주소를 결정한다."""
    host_override = os.getenv("SARI_DAEMON_HOST", "").strip()
    port_override = os.getenv("SARI_DAEMON_PORT", "").strip()
    force_override = os.getenv("SARI_DAEMON_OVERRIDE", "").strip().lower() in {"1", "true", "yes", "on"}

    if force_override and host_override != "" and port_override != "":
        return host_override, int(port_override)

    if workspace_root is not None and workspace_root.strip() != "":
        registry_repo = DaemonRegistryRepository(db_path)
        entry = registry_repo.resolve_latest(workspace_root.strip())
        if entry is not None:
            return entry.host, entry.port

    runtime_repo = RuntimeRepository(db_path)
    runtime = runtime_repo.get_runtime()
    if runtime is not None:
        return runtime.host, runtime.port

    if host_override != "" and port_override != "":
        return host_override, int(port_override)

    return "127.0.0.1", 47777

