"""HTTP 계층에서 사용하는 최소 포트(Protocol) 모음."""

from __future__ import annotations

from typing import Protocol


class RuntimeRepoPort(Protocol):
    """런타임 세션 계수 및 상태 조회 포트."""

    def get_runtime(self) -> object: ...
    def increment_session(self) -> None: ...
    def decrement_session(self) -> None: ...


class WorkspaceRepoPort(Protocol):
    """워크스페이스 조회 포트."""

    def list_all(self) -> list[object]: ...
    def get_by_path(self, path: str) -> object | None: ...


class LanguageProbeRepoPort(Protocol):
    """언어 probe 스냅샷 조회 포트."""

    def list_all(self) -> list[object]: ...


class RepoRegistryRepoPort(Protocol):
    """repo registry 갱신 포트."""

    def upsert(self, payload: object) -> object: ...
