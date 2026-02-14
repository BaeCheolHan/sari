from collections.abc import Mapping
from typing import Optional, TypeAlias

from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.config import Config
from sari.mcp.tools._util import ErrorCode, invalid_args_response, mcp_response, pack_header, pack_line

ToolResult: TypeAlias = dict[str, object]

def _row_get(row: object, key: str, index: int, default: object = 0) -> object:
    if row is None:
        return default
    try:
        if hasattr(row, "keys"):
            return row[key]
    except Exception:
        pass
    if isinstance(row, (list, tuple)) and len(row) > index:
        return row[index]
    return default


def _status_field(status: object, name: str, default: object) -> object:
    return getattr(status, name, default) if status is not None else default


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return False


def execute_status(
    args: Mapping[str, object],
    indexer: Optional[Indexer],
    db: Optional[LocalSearchDB],
    cfg: Optional[Config],
    workspace_root: str,
    server_version: str,
    logger: object = None,
) -> ToolResult:
    """
    Sari 서버의 상태를 조회하는 현대화된 상태 도구입니다.
    인덱서 및 DB의 실시간 상태와 풍부한 메타데이터를 제공합니다.
    """
    if not isinstance(args, Mapping):
        return invalid_args_response("status", "args must be an object")

    # DB 통계 정보 수집
    total_symbols = 0
    total_files = 0
    db_error = ""
    if db:
        try:
            files_row = db.db.execute_sql("SELECT COUNT(1) AS count_files FROM files").fetchone()
            symbols_row = db.db.execute_sql("SELECT COUNT(1) AS count_symbols FROM symbols").fetchone()
            total_files = int(_row_get(files_row, "count_files", 0, 0) or 0)
            total_symbols = int(_row_get(symbols_row, "count_symbols", 0, 0) or 0)
        except Exception:
            db_error = "DB access failed"
    else:
        db_error = "DB not connected"

    status_obj = getattr(indexer, "status", None) if indexer is not None else None
    runtime_status = {}
    if indexer is not None and hasattr(indexer, "get_runtime_status"):
        try:
            raw_runtime = indexer.get_runtime_status()
            if isinstance(raw_runtime, dict):
                runtime_status = raw_runtime
        except Exception:
            runtime_status = {}
    status_data: ToolResult = {
        "index_ready": _coerce_bool(runtime_status.get("index_ready", _status_field(status_obj, "index_ready", False))),
        "indexed_files": _coerce_int(runtime_status.get("indexed_files", _status_field(status_obj, "indexed_files", 0))),
        "scanned_files": _coerce_int(runtime_status.get("scanned_files", _status_field(status_obj, "scanned_files", 0))),
        "symbols_extracted": _coerce_int(runtime_status.get("symbols_extracted", _status_field(status_obj, "symbols_extracted", 0))),
        "errors": _coerce_int(runtime_status.get("errors", _status_field(status_obj, "errors", 0))),
        "total_files_db": total_files,
        "total_symbols_db": total_symbols,
        "db_error": db_error,
        "server_version": server_version,
        "workspace_root": workspace_root,
        "status_source": runtime_status.get("status_source", "indexer_status"),
        "db_engine": "PeeWee+Turbo",
        "fts_enabled": True,
        "cfg_include_ext": ",".join(cfg.include_ext) if cfg and cfg.include_ext else "",
        "cache_hit_rate": float(runtime_status.get("cache_hit_rate", 0.0) or 0.0),
        "queue_p95_latency": float(runtime_status.get("queue_p95_latency", 0.0) or 0.0),
        "conflict_count": _coerce_int(runtime_status.get("conflict_count", 0), 0),
    }
    try:
        from sari.core.lsp.hub import get_lsp_hub

        lsp_metrics = get_lsp_hub().metrics_snapshot()
        status_data["language_cold_start_count"] = _coerce_int(lsp_metrics.get("language_cold_start_count"), 0)
        status_data["lsp_restart_count"] = _coerce_int(lsp_metrics.get("lsp_restart_count"), 0)
        status_data["lsp_timeout_rate"] = float(lsp_metrics.get("lsp_timeout_rate", 0.0) or 0.0)
        status_data["lsp_backpressure_count"] = _coerce_int(lsp_metrics.get("lsp_backpressure_count"), 0)
        status_data["lsp_by_language"] = lsp_metrics.get("by_language", {})
    except Exception:
        status_data["language_cold_start_count"] = 0
        status_data["lsp_restart_count"] = 0
        status_data["lsp_timeout_rate"] = 0.0
        status_data["lsp_backpressure_count"] = 0
        status_data["lsp_by_language"] = {}
    
    # 풍부한 정보를 담은 PACK1 응답 생성
    def build_pack() -> str:
        lines = [pack_header("status", {}, returned=len(status_data))]
        for k, v in status_data.items():
            val = str(v).lower() if isinstance(v, bool) else str(v)
            lines.append(pack_line("m", {k: val}))
        if db_error:
            lines.append(pack_line("m", {"reason_code": ErrorCode.DB_ERROR.value}))
        return "\n".join(lines)

    def build_json() -> ToolResult:
        payload: ToolResult = dict(status_data)
        if db_error:
            payload["error"] = {"code": ErrorCode.DB_ERROR.value, "message": db_error}
        payload["ok"] = True
        return payload

    return mcp_response("status", build_pack, build_json)
