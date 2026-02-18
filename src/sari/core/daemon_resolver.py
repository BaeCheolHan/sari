"""데몬 엔드포인트 해석 유틸을 제공한다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.runtime_repository import RuntimeRepository


@dataclass(frozen=True)
class DaemonAddressResolutionDTO:
    """데몬 주소 해석 결과 DTO다."""

    host: str
    port: int
    reason: str


def resolve_daemon_address(db_path: Path, workspace_root: str | None = None) -> tuple[str, int]:
    """레지스트리 우선으로 데몬 주소를 결정한다."""
    resolved = resolve_daemon_endpoint(db_path=db_path, workspace_root=workspace_root)
    return resolved.host, resolved.port


def resolve_daemon_endpoint(db_path: Path, workspace_root: str | None = None) -> DaemonAddressResolutionDTO:
    """레지스트리 우선으로 데몬 주소와 선택 근거를 결정한다."""
    host_override = os.getenv("SARI_DAEMON_HOST", "").strip()
    port_override = os.getenv("SARI_DAEMON_PORT", "").strip()
    force_override = os.getenv("SARI_DAEMON_OVERRIDE", "").strip().lower() in {"1", "true", "yes", "on"}

    if force_override and host_override != "" and port_override != "":
        return DaemonAddressResolutionDTO(host=host_override, port=int(port_override), reason="force_override")

    if workspace_root is not None and workspace_root.strip() != "":
        registry_repo = DaemonRegistryRepository(db_path)
        entry = registry_repo.resolve_latest(workspace_root.strip())
        if entry is not None:
            return DaemonAddressResolutionDTO(host=entry.host, port=entry.port, reason="registry_active")

    runtime_repo = RuntimeRepository(db_path)
    runtime = runtime_repo.get_runtime()
    if runtime is not None:
        return DaemonAddressResolutionDTO(host=runtime.host, port=runtime.port, reason="runtime")

    if host_override != "" and port_override != "":
        return DaemonAddressResolutionDTO(host=host_override, port=int(port_override), reason="env_fallback")

    return DaemonAddressResolutionDTO(host="127.0.0.1", port=47777, reason="default")
