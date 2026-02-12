from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class FsEventKind(str, Enum):
    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    MOVED = "MOVED"


@dataclass(frozen=True)
class FsEvent:
    kind: FsEventKind
    path: str
    root: str = "" # Added field
    dest_path: Optional[str] = None
    ts: float = 0.0


class TaskAction(str, Enum):
    INDEX = "INDEX"
    DELETE = "DELETE"


@dataclass
class CoalesceTask:
    action: TaskAction
    path: str
    attempts: int = 0
    enqueue_ts: float = 0.0
    last_seen: float = 0.0


@dataclass
class DbTask:
    kind: str
    rows: Optional[List[object]] = None
    path: Optional[str] = None
    paths: Optional[List[str]] = None
    ts: Optional[int] = None
    repo_meta: Optional[dict] = None
    engine_docs: Optional[List[dict]] = None
    engine_deletes: Optional[List[str]] = None
    snippet_rows: Optional[List[object]] = None
    context_rows: Optional[List[object]] = None
    failed_rows: Optional[List[object]] = None
    failed_paths: Optional[List[str]] = None
    failed_updates: Optional[List[object]] = None


def coalesce_action(existing: Optional[TaskAction], incoming: TaskAction) -> TaskAction:
    # The latest event should win for a path to avoid stale DELETE dominance
    # when a file is recreated immediately after deletion.
    if existing is None:
        return incoming
    if incoming == TaskAction.INDEX:
        return TaskAction.INDEX
    return TaskAction.DELETE


def split_moved_event(event: FsEvent) -> List[CoalesceTask]:
    if event.kind != FsEventKind.MOVED:
        return []
    actions: List[CoalesceTask] = []
    if event.path:
        actions.append(CoalesceTask(action=TaskAction.DELETE, path=event.path))
    if event.dest_path:
        actions.append(CoalesceTask(action=TaskAction.INDEX, path=event.dest_path))
    return actions
