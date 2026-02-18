from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
@dataclass(frozen=True)
class WorkspaceDTO:
    path: str
    name: str | None
    indexed_at: str | None
    is_active: bool
    def to_sql_params(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": self.name,
            "indexed_at": self.indexed_at,
            "is_active": 1 if self.is_active else 0,
        }
Workspace = WorkspaceDTO
@dataclass(frozen=True)
class DaemonRuntimeDTO:
    pid: int
    host: str
    port: int
    state: str
    started_at: str
    session_count: int
    last_heartbeat_at: str
    last_exit_reason: str | None = None
    def to_sql_params(self) -> dict[str, object]:
        return {
            "singleton_key": "default",
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "state": self.state,
            "started_at": self.started_at,
            "session_count": self.session_count,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_exit_reason": self.last_exit_reason,
        }
@dataclass(frozen=True)
class DaemonRegistryEntryDTO:
    daemon_id: str
    host: str
    port: int
    pid: int
    workspace_root: str
    protocol: str
    started_at: str
    last_seen_at: str
    is_draining: bool
    deployment_state: str = "ACTIVE"
    health_fail_streak: int = 0
    last_health_error: str | None = None
    last_health_at: str | None = None
    def to_sql_params(self) -> dict[str, object]:
        return {
            "daemon_id": self.daemon_id,
            "host": self.host,
            "port": self.port,
            "pid": self.pid,
            "workspace_root": self.workspace_root,
            "protocol": self.protocol,
            "started_at": self.started_at,
            "last_seen_at": self.last_seen_at,
            "is_draining": 1 if self.is_draining else 0,
            "deployment_state": self.deployment_state,
            "health_fail_streak": self.health_fail_streak,
            "last_health_error": self.last_health_error,
            "last_health_at": self.last_health_at,
        }
@dataclass(frozen=True)
class HealthResponseDTO:
    status: str
    version: str
    uptime_sec: float
@dataclass(frozen=True)
class ErrorResponseDTO:
    code: str
    message: str
@dataclass(frozen=True)
class LanguageProbeStatusDTO:
    language: str
    enabled: bool
    available: bool
    last_probe_at: str | None
    last_error_code: str | None
    last_error_message: str | None
    updated_at: str
    symbol_extract_success: bool = False
    document_symbol_count: int = 0
    path_mapping_ok: bool = False
    timeout_occurred: bool = False
    recovered_by_restart: bool = False
    provisioning_mode: str | None = None
    missing_dependency: str | None = None
    install_hint: str | None = None
    def to_sql_params(self) -> dict[str, object]:
        return {
            "language": self.language,
            "enabled": 1 if self.enabled else 0,
            "available": 1 if self.available else 0,
            "last_probe_at": self.last_probe_at,
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "symbol_extract_success": 1 if self.symbol_extract_success else 0,
            "document_symbol_count": self.document_symbol_count,
            "path_mapping_ok": 1 if self.path_mapping_ok else 0,
            "timeout_occurred": 1 if self.timeout_occurred else 0,
            "recovered_by_restart": 1 if self.recovered_by_restart else 0,
            "updated_at": self.updated_at,
        }
    def to_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "enabled": self.enabled,
            "available": self.available,
            "last_probe_at": self.last_probe_at,
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "symbol_extract_success": self.symbol_extract_success,
            "document_symbol_count": self.document_symbol_count,
            "path_mapping_ok": self.path_mapping_ok,
            "timeout_occurred": self.timeout_occurred,
            "recovered_by_restart": self.recovered_by_restart,
            "provisioning_mode": self.provisioning_mode,
            "missing_dependency": self.missing_dependency,
            "install_hint": self.install_hint,
        }
@dataclass(frozen=True)
class SearchErrorDTO:
    code: str
    message: str
    severity: str
    origin: str
    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "origin": self.origin,
        }
@dataclass(frozen=True)
class SearchPlaceholderResponseDTO:
    phase: str
    message: str
    query: str
    limit: int
@dataclass(frozen=True)
class CandidateFileDTO:
    repo_root: str
    relative_path: str
    score: float
    file_hash: str
@dataclass(frozen=True)
class CandidateIndexChangeDTO:
    repo_root: str
    relative_path: str
    absolute_path: str
    content_hash: str
    mtime_ns: int
    size_bytes: int
    event_source: str
    recorded_at: str
    def to_sql_params(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "relative_path": self.relative_path,
            "absolute_path": self.absolute_path,
            "content_hash": self.content_hash,
            "mtime_ns": self.mtime_ns,
            "size_bytes": self.size_bytes,
            "event_source": self.event_source,
            "recorded_at": self.recorded_at,
        }
@dataclass(frozen=True)
class CandidateIndexChangeLogDTO:
    change_id: int
    change_type: str
    status: str
    repo_root: str
    relative_path: str
    absolute_path: str | None
    content_hash: str | None
    mtime_ns: int | None
    size_bytes: int | None
    event_source: str
    reason: str | None
    created_at: str
    updated_at: str
@dataclass(frozen=True)
class RankingComponentsDTO:
    """검색 점수 구성요소를 표현한다."""

    rrf: float = 0.0
    importance: float = 0.0
    vector: float = 0.0
    hierarchy: float = 0.0
    final: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """직렬화 가능한 점수 구성요소 딕셔너리를 반환한다."""
        return {
            "rrf": self.rrf,
            "importance": self.importance,
            "vector": self.vector,
            "hierarchy": self.hierarchy,
            "final": self.final,
        }


@dataclass(frozen=True)
class SearchItemDTO:
    item_type: str
    repo: str
    relative_path: str
    score: float
    source: str
    name: str | None
    kind: str | None
    content_hash: str | None = None
    rrf_score: float = 0.0
    importance_score: float = 0.0
    base_rrf_score: float = 0.0
    importance_norm_score: float = 0.0
    vector_norm_score: float = 0.0
    hierarchy_score: float = 0.0
    hierarchy_norm_score: float = 0.0
    symbol_key: str | None = None
    parent_symbol_key: str | None = None
    depth: int = 0
    container_name: str | None = None
    ranking_components: RankingComponentsDTO | None = None
    vector_score: float | None = None
    blended_score: float = 0.0
    final_score: float = 0.0
@dataclass(frozen=True)
class CollectedFileL1DTO:
    repo_root: str
    relative_path: str
    absolute_path: str
    repo_label: str
    mtime_ns: int
    size_bytes: int
    content_hash: str
    is_deleted: bool
    last_seen_at: str
    updated_at: str
    enrich_state: str
    def to_sql_params(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "relative_path": self.relative_path,
            "absolute_path": self.absolute_path,
            "repo_label": self.repo_label,
            "mtime_ns": self.mtime_ns,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "is_deleted": 1 if self.is_deleted else 0,
            "last_seen_at": self.last_seen_at,
            "updated_at": self.updated_at,
            "enrich_state": self.enrich_state,
        }
@dataclass(frozen=True)
class FileEnrichJobDTO:
    job_id: str
    repo_root: str
    relative_path: str
    content_hash: str
    priority: int
    enqueue_source: str
    status: str
    attempt_count: int
    last_error: str | None
    next_retry_at: str
    created_at: str
    updated_at: str
    def to_sql_params(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "repo_root": self.repo_root,
            "relative_path": self.relative_path,
            "content_hash": self.content_hash,
            "priority": self.priority,
            "enqueue_source": self.enqueue_source,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "last_error": self.last_error,
            "next_retry_at": self.next_retry_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
@dataclass(frozen=True)
class FileEnrichFailureUpdateDTO:
    job_id: str
    error_message: str
    now_iso: str
    dead_threshold: int
    backoff_base_sec: int
@dataclass(frozen=True)
class EnqueueRequestDTO:
    repo_root: str
    relative_path: str
    content_hash: str
    priority: int
    enqueue_source: str
    now_iso: str
@dataclass(frozen=True)
class CollectedFileBodyDTO:
    repo_root: str
    relative_path: str
    content_hash: str
    content_zlib: bytes
    content_len: int
    normalized_text: str
    created_at: str
    updated_at: str
    def to_sql_params(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "relative_path": self.relative_path,
            "content_hash": self.content_hash,
            "content_zlib": self.content_zlib,
            "content_len": self.content_len,
            "normalized_text": self.normalized_text,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
@dataclass(frozen=True)
class ToolReadinessStateDTO:
    repo_root: str
    relative_path: str
    content_hash: str
    list_files_ready: bool
    read_file_ready: bool
    search_symbol_ready: bool
    get_callers_ready: bool
    consistency_ready: bool
    quality_ready: bool
    tool_ready: bool
    last_reason: str
    updated_at: str
    def to_sql_params(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "relative_path": self.relative_path,
            "content_hash": self.content_hash,
            "list_files_ready": 1 if self.list_files_ready else 0,
            "read_file_ready": 1 if self.read_file_ready else 0,
            "search_symbol_ready": 1 if self.search_symbol_ready else 0,
            "get_callers_ready": 1 if self.get_callers_ready else 0,
            "consistency_ready": 1 if self.consistency_ready else 0,
            "quality_ready": 1 if self.quality_ready else 0,
            "tool_ready": 1 if self.tool_ready else 0,
            "last_reason": self.last_reason,
            "updated_at": self.updated_at,
        }
@dataclass(frozen=True)
class EnrichStateUpdateDTO:
    repo_root: str
    relative_path: str
    enrich_state: str
    updated_at: str
    def to_sql_params(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "relative_path": self.relative_path,
            "enrich_state": self.enrich_state,
            "updated_at": self.updated_at,
        }
@dataclass(frozen=True)
class FileBodyDeleteTargetDTO:
    repo_root: str
    relative_path: str
    content_hash: str
    def to_sql_params(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "relative_path": self.relative_path,
            "content_hash": self.content_hash,
        }
@dataclass(frozen=True)
class LspExtractPersistDTO:
    repo_root: str
    relative_path: str
    content_hash: str
    symbols: list[dict[str, object]]
    relations: list[dict[str, object]]
    created_at: str
@dataclass(frozen=True)
class FileListItemDTO:
    repo: str
    relative_path: str
    size_bytes: int
    mtime_ns: int
    content_hash: str
    enrich_state: str
@dataclass(frozen=True)
class FileReadResultDTO:
    relative_path: str
    content: str
    start_line: int
    end_line: int
    source: str
    total_lines: int
    is_truncated: bool
    next_offset: int | None
@dataclass(frozen=True)
class CollectionScanRepoResultDTO:
    repo_root: str
    scanned_count: int
    indexed_count: int
    deleted_count: int
    status: str = "ok"
    error_code: str | None = None
    error_message: str | None = None
    def to_dict(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "scanned_count": self.scanned_count,
            "indexed_count": self.indexed_count,
            "deleted_count": self.deleted_count,
            "status": self.status,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

@dataclass(frozen=True)
class CollectionScanResultDTO:
    scanned_count: int
    indexed_count: int
    deleted_count: int
    mode: str = "single_repo"
    target_repo_count: int = 1
    succeeded_repo_count: int = 1
    failed_repo_count: int = 0
    repo_results: tuple[CollectionScanRepoResultDTO, ...] = ()
@dataclass(frozen=True)
class CollectionPolicyDTO:
    include_ext: tuple[str, ...]
    exclude_globs: tuple[str, ...]
    max_file_size_bytes: int
    scan_interval_sec: int
    max_enrich_batch: int
    retry_max_attempts: int
    retry_backoff_base_sec: int
    queue_poll_interval_ms: int
@dataclass(frozen=True)
class PipelineMetricsDTO:
    queue_depth: int
    running_jobs: int
    failed_jobs: int
    dead_jobs: int
    done_jobs: int
    avg_enrich_latency_ms: float
    indexing_mode: str = "steady"
    l2_coverage_bps: int = 0
    l3_coverage_bps: int = 0
    l3_backlog_count: int = 0
    progress_percent_l2: float = 0.0
    progress_percent_l3: float = 0.0
    eta_l2_sec: int = -1
    eta_l3_sec: int = -1
    eta_confidence_bps: int = 0
    eta_window_sec: int = 0
    throughput_ema: float = 0.0
    remaining_jobs_l2: int = 0
    remaining_jobs_l3: int = 0
    worker_state: str = "running"
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_error_at: str | None = None
    watcher_queue_depth: int = 0
    watcher_drop_count: int = 0
    watcher_overflow_count: int = 0
    watcher_last_overflow_at: str | None = None
    lsp_instance_count: int = 0
    lsp_forced_kill_count: int = 0
    lsp_stop_timeout_count: int = 0
    lsp_orphan_suspect_count: int = 0
    def to_dict(self) -> dict[str, object]:
        return {
            "queue_depth": self.queue_depth,
            "running_jobs": self.running_jobs,
            "failed_jobs": self.failed_jobs,
            "dead_jobs": self.dead_jobs,
            "done_jobs": self.done_jobs,
            "avg_enrich_latency_ms": self.avg_enrich_latency_ms,
            "indexing_mode": self.indexing_mode,
            "l2_coverage_bps": self.l2_coverage_bps,
            "l3_coverage_bps": self.l3_coverage_bps,
            "l3_backlog_count": self.l3_backlog_count,
            "progress_percent_l2": self.progress_percent_l2,
            "progress_percent_l3": self.progress_percent_l3,
            "eta_l2_sec": self.eta_l2_sec,
            "eta_l3_sec": self.eta_l3_sec,
            "eta_confidence_bps": self.eta_confidence_bps,
            "eta_window_sec": self.eta_window_sec,
            "throughput_ema": self.throughput_ema,
            "remaining_jobs_l2": self.remaining_jobs_l2,
            "remaining_jobs_l3": self.remaining_jobs_l3,
            "worker_state": self.worker_state,
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "last_error_at": self.last_error_at,
            "watcher_queue_depth": self.watcher_queue_depth,
            "watcher_drop_count": self.watcher_drop_count,
            "watcher_overflow_count": self.watcher_overflow_count,
            "watcher_last_overflow_at": self.watcher_last_overflow_at,
            "lsp_instance_count": self.lsp_instance_count,
            "lsp_forced_kill_count": self.lsp_forced_kill_count,
            "lsp_stop_timeout_count": self.lsp_stop_timeout_count,
            "lsp_orphan_suspect_count": self.lsp_orphan_suspect_count,
        }
@dataclass(frozen=True)
class PipelineErrorEventDTO:
    event_id: str
    occurred_at: str
    component: str
    phase: str
    severity: str
    scope_type: str
    repo_root: str | None
    relative_path: str | None
    job_id: str | None
    attempt_count: int
    error_code: str
    error_message: str
    error_type: str
    stacktrace_text: str
    context_json: str
    worker_name: str
    run_mode: str
    resolved: bool
    resolved_at: str | None
    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "component": self.component,
            "phase": self.phase,
            "severity": self.severity,
            "scope_type": self.scope_type,
            "repo_root": self.repo_root,
            "relative_path": self.relative_path,
            "job_id": self.job_id,
            "attempt_count": self.attempt_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "stacktrace_text": self.stacktrace_text,
            "context_json": self.context_json,
            "worker_name": self.worker_name,
            "run_mode": self.run_mode,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at,
        }
@dataclass(frozen=True)
class PipelinePolicyDTO:
    deletion_hold: bool
    l3_p95_threshold_ms: int
    dead_ratio_threshold_bps: int
    enrich_worker_count: int
    updated_at: str
    watcher_queue_max: int = 10000
    watcher_overflow_rescan_cooldown_sec: int = 30
    bootstrap_mode_enabled: bool = False
    bootstrap_l3_worker_count: int = 1
    bootstrap_l3_queue_max: int = 1000
    bootstrap_exit_min_l2_coverage_bps: int = 9500
    bootstrap_exit_max_sec: int = 1800
    def to_dict(self) -> dict[str, object]:
        return {
            "deletion_hold": self.deletion_hold,
            "l3_p95_threshold_ms": self.l3_p95_threshold_ms,
            "dead_ratio_threshold_bps": self.dead_ratio_threshold_bps,
            "enrich_worker_count": self.enrich_worker_count,
            "watcher_queue_max": self.watcher_queue_max,
            "watcher_overflow_rescan_cooldown_sec": self.watcher_overflow_rescan_cooldown_sec,
            "bootstrap_mode_enabled": self.bootstrap_mode_enabled,
            "bootstrap_l3_worker_count": self.bootstrap_l3_worker_count,
            "bootstrap_l3_queue_max": self.bootstrap_l3_queue_max,
            "bootstrap_exit_min_l2_coverage_bps": self.bootstrap_exit_min_l2_coverage_bps,
            "bootstrap_exit_max_sec": self.bootstrap_exit_max_sec,
            "updated_at": self.updated_at,
        }
@dataclass(frozen=True)
class DeadJobItemDTO:
    job_id: str
    repo_root: str
    relative_path: str
    attempt_count: int
    last_error: str | None
    updated_at: str
    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "repo_root": self.repo_root,
            "relative_path": self.relative_path,
            "attempt_count": self.attempt_count,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }
@dataclass(frozen=True)
class DeadJobActionResultDTO:
    requeued_count: int = 0
    purged_count: int = 0
    queue_snapshot: dict[str, int] | None = None
    executed_at: str | None = None
    repo_scope: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "requeued_count": self.requeued_count,
            "purged_count": self.purged_count,
        }
        if self.queue_snapshot is not None:
            payload["queue_snapshot"] = self.queue_snapshot
        if self.executed_at is not None:
            payload["executed_at"] = self.executed_at
        if self.repo_scope is not None:
            payload["repo_scope"] = self.repo_scope
        return payload
@dataclass(frozen=True)
class PipelineAlertSnapshotDTO:
    state: str
    window_seconds: int
    event_count: int
    dead_count: int
    dead_ratio_bps: int
    l3_p95_ms: int
    threshold_dead_ratio_bps: int
    threshold_l3_p95_ms: int
    policy: PipelinePolicyDTO
    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "window_seconds": self.window_seconds,
            "event_count": self.event_count,
            "dead_count": self.dead_count,
            "dead_ratio_bps": self.dead_ratio_bps,
            "l3_p95_ms": self.l3_p95_ms,
            "threshold_dead_ratio_bps": self.threshold_dead_ratio_bps,
            "threshold_l3_p95_ms": self.threshold_l3_p95_ms,
            "policy": self.policy.to_dict(),
        }
@dataclass(frozen=True)
class PipelineAutoControlStateDTO:
    auto_hold_enabled: bool
    auto_hold_active: bool
    last_action: str
    updated_at: str
    def to_dict(self) -> dict[str, object]:
        return {
            "auto_hold_enabled": self.auto_hold_enabled,
            "auto_hold_active": self.auto_hold_active,
            "last_action": self.last_action,
            "updated_at": self.updated_at,
        }
@dataclass(frozen=True)
class SymbolSearchItemDTO:
    repo: str
    relative_path: str
    name: str
    kind: str
    line: int
    end_line: int
    content_hash: str
    symbol_key: str | None = None
    parent_symbol_key: str | None = None
    depth: int = 0
    container_name: str | None = None
    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "relative_path": self.relative_path,
            "name": self.name,
            "kind": self.kind,
            "line": self.line,
            "end_line": self.end_line,
            "content_hash": self.content_hash,
            "symbol_key": self.symbol_key,
            "parent_symbol_key": self.parent_symbol_key,
            "depth": self.depth,
            "container_name": self.container_name,
        }
@dataclass(frozen=True)
class CallerEdgeDTO:
    repo: str
    relative_path: str
    from_symbol: str
    to_symbol: str
    line: int
    content_hash: str
    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "relative_path": self.relative_path,
            "from_symbol": self.from_symbol,
            "to_symbol": self.to_symbol,
            "line": self.line,
            "content_hash": self.content_hash,
        }
@dataclass(frozen=True)
class L3ReferenceDataDTO:
    symbols: list[dict[str, object]]
    relations: list[dict[str, object]]
    error_message: str | None
    def has_error(self) -> bool:
        return self.error_message is not None and self.error_message.strip() != ""
@dataclass(frozen=True)
class L3DiffResultDTO:
    symbol_tp: int
    symbol_fp: int
    symbol_fn: int
    caller_tp: int
    caller_fp: int
    caller_fn: int
    error_message: str | None
    def to_dict(self) -> dict[str, object]:
        return {
            "symbol_tp": self.symbol_tp,
            "symbol_fp": self.symbol_fp,
            "symbol_fn": self.symbol_fn,
            "caller_tp": self.caller_tp,
            "caller_fp": self.caller_fp,
            "caller_fn": self.caller_fn,
            "error_message": self.error_message,
        }
@dataclass(frozen=True)
class SnippetSaveDTO:
    repo_root: str
    source_path: str
    start_line: int
    end_line: int
    tag: str
    note: str | None
    commit_hash: str | None
    content_text: str
    created_at: str
    def to_sql_params(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "source_path": self.source_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "tag": self.tag,
            "note": self.note,
            "commit_hash": self.commit_hash,
            "content_text": self.content_text,
            "created_at": self.created_at,
        }
@dataclass(frozen=True)
class SnippetRecordDTO:
    snippet_id: int
    repo_root: str
    source_path: str
    start_line: int
    end_line: int
    tag: str
    note: str | None
    commit_hash: str | None
    content_text: str
    created_at: str
    def to_dict(self) -> dict[str, object]:
        return {
            "snippet_id": self.snippet_id,
            "repo": self.repo_root,
            "path": self.source_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "tag": self.tag,
            "note": self.note,
            "commit": self.commit_hash,
            "content": self.content_text,
            "created_at": self.created_at,
        }
@dataclass(frozen=True)
class KnowledgeEntryDTO:
    kind: str
    repo_root: str
    topic: str
    content_text: str
    tags: tuple[str, ...]
    related_files: tuple[str, ...]
    created_at: str
    def to_sql_params(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "repo_root": self.repo_root,
            "topic": self.topic,
            "content_text": self.content_text,
            "tags_json": json.dumps(list(self.tags), ensure_ascii=False),
            "related_files_json": json.dumps(list(self.related_files), ensure_ascii=False),
            "created_at": self.created_at,
        }
@dataclass(frozen=True)
class KnowledgeRecordDTO:
    entry_id: int
    kind: str
    repo_root: str
    topic: str
    content_text: str
    tags: tuple[str, ...]
    related_files: tuple[str, ...]
    created_at: str
    def to_dict(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "kind": self.kind,
            "repo": self.repo_root,
            "topic": self.topic,
            "content": self.content_text,
            "tags": list(self.tags),
            "related_files": list(self.related_files),
            "created_at": self.created_at,
        }
def now_iso8601_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
