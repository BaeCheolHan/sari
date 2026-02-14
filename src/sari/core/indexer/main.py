import os
import time
import json
import inspect
import logging
import threading
import multiprocessing
import tempfile
import traceback
import concurrent.futures
from collections import OrderedDict
from typing import Optional, Callable
from pathlib import Path
import uuid
from sari.core.config.main import Config
from sari.core.db.main import LocalSearchDB
from .worker import IndexWorker, init_process_worker, process_file_task_in_process
from .scanner import Scanner
from sari.core.workspace import WorkspaceManager
from sari.core.utils.path import PathUtils

from sari.core.models import IndexingResult

_PROCESS_POOL_ALLOWED: Optional[bool] = None
try:
    import psutil as _psutil  # type: ignore
except Exception:
    _psutil = None


def _is_pid_alive(pid: int) -> bool:
    """Return True when the target PID is alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _cleanup_deleted_paths(
    db: LocalSearchDB,
    scanned_root_ids: list[str],
    *,
    now_ts: int,
    logger: Optional[logging.Logger] = None,
) -> int:
    root_ids = [rid for rid in scanned_root_ids if rid]
    if not root_ids:
        return 0
    conn = db.get_connection()
    placeholders = ",".join(["?"] * len(root_ids))
    deleted_ts = int(time.time())
    started_tx = not bool(getattr(conn, "in_transaction", False))
    changed = 0
    try:
        if started_tx:
            conn.execute("BEGIN IMMEDIATE TRANSACTION")
        cur = conn.execute(
            f"""
            UPDATE files
               SET deleted_ts = ?, status = 'deleted'
             WHERE deleted_ts = 0
               AND last_seen_ts < ?
               AND root_id IN ({placeholders})
            """,
            tuple([deleted_ts, now_ts] + root_ids),
        )
        try:
            changed = int(getattr(cur, "rowcount", 0) or 0)
        except (TypeError, ValueError):
            changed = 0
        conn.execute(
            f"""
            DELETE FROM symbols
             WHERE root_id IN ({placeholders})
               AND path IN (
                    SELECT path FROM files
                     WHERE deleted_ts > 0
                       AND root_id IN ({placeholders})
               )
            """,
            tuple(root_ids + root_ids),
        )
        conn.execute(
            f"""
            DELETE FROM symbol_relations
             WHERE (from_root_id IN ({placeholders}) AND from_path IN (
                        SELECT path FROM files
                         WHERE deleted_ts > 0
                           AND root_id IN ({placeholders})
                    ))
                OR (to_root_id IN ({placeholders}) AND to_path IN (
                        SELECT path FROM files
                         WHERE deleted_ts > 0
                           AND root_id IN ({placeholders})
                    ))
            """,
            tuple(root_ids + root_ids + root_ids + root_ids),
        )
        db.update_stats()
        if started_tx:
            conn.execute("COMMIT")
        return changed
    except Exception:
        if started_tx:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        raise


def _adaptive_flush_threshold(
    base: int,
    *,
    pending_count: int,
    max_inflight: int,
    enabled: bool,
) -> int:
    norm_base = max(1, int(base or 1))
    if not enabled:
        return norm_base
    if max_inflight <= 0:
        return norm_base
    try:
        load = float(pending_count) / float(max_inflight)
    except Exception:
        load = 0.0
    if load >= 0.80:
        return max(1, norm_base // 2)
    if load <= 0.20:
        return max(1, min(norm_base * 2, norm_base + 5000))
    return norm_base


def _default_laptop_max_workers() -> int:
    cpu = int(os.cpu_count() or 4)
    try:
        reserve = max(1, int(os.environ.get("SARI_INDEXER_RESERVE_CORES", "2") or 2))
    except Exception:
        reserve = 2
    try:
        hard_cap = max(1, int(os.environ.get("SARI_INDEXER_MAX_WORKERS_CAP", "8") or 8))
    except Exception:
        hard_cap = 8
    workers = max(1, cpu - reserve)
    # On small laptops, keep at least 2 workers but don't exceed cap.
    workers = max(2 if cpu >= 2 else 1, workers)
    return max(1, min(workers, hard_cap))


def _default_laptop_max_inflight(max_workers: int) -> int:
    # Lower inflight depth to reduce memory pressure and UI contention.
    return max(int(max_workers), min(int(max_workers) * 2, 32))


def _update_cpu_throttle_state(
    active: bool,
    cpu_percent: float,
    *,
    high_watermark: float,
    resume_watermark: float,
) -> bool:
    if active:
        return float(cpu_percent) > float(resume_watermark)
    return float(cpu_percent) >= float(high_watermark)


def _effective_inflight_limit(
    max_inflight: int,
    max_workers: int,
    *,
    throttle_active: bool,
    throttle_workers: int,
) -> int:
    base = max(1, int(max_inflight or 1))
    if not throttle_active:
        return base
    tw = max(1, int(throttle_workers or 1))
    # Keep backlog shallow under contention.
    return max(1, min(base, max(tw, tw * 2, max_workers // 2)))


def _read_system_cpu_percent() -> Optional[float]:
    if _psutil is None:
        return None
    try:
        return float(_psutil.cpu_percent(interval=None))
    except Exception:
        return None


def _apply_incremental_low_impact_caps(
    max_workers: int,
    max_inflight: int,
    *,
    incremental_mode: bool,
    enabled: bool,
) -> tuple[int, int]:
    if not enabled or not incremental_mode:
        return max(1, int(max_workers or 1)), max(1, int(max_inflight or 1))
    workers = max(1, min(int(max_workers or 1), 4))
    inflight = max(workers, min(int(max_inflight or workers), 8))
    return workers, inflight


def _scan_to_db(config: Config, db: LocalSearchDB,
                logger: logging.Logger,
                parent_pid: Optional[int] = None,
                parent_alive_check: Optional[Callable[[int], bool]] = None,
                progress_callback: Optional[Callable[[dict[str, object]], None]] = None) -> dict[str, object]:
    """
    파일 시스템을 스캔하여 변경된 파일을 감지하고 데이터베이스에 인덱싱합니다.
    이 함수는 별도의 프로세스(Worker) 내에서 실행될 수 있으며, 결과를 Status 딕셔너리로 반환합니다.
    """
    status = {
        "scan_started_ts": int(time.time()),
        "scan_finished_ts": 0,
        "scanned_files": 0,
        "indexed_files": 0,
        "symbols_extracted": 0,
        "symbols_deferred_files": 0,
        "errors": 0,
        "index_version": "",
        "adaptive_flush_enabled": False,
    }
    worker = IndexWorker(config, db, logger, None)
    try:
        _worker_accepts_skip_prev_lookup = "skip_prev_lookup" in inspect.signature(
            worker.process_file_task
        ).parameters
    except Exception:
        _worker_accepts_skip_prev_lookup = True
    prev_turbo_update_stats_enabled = True
    if hasattr(db, "set_turbo_update_stats_enabled"):
        try:
            prev_turbo_update_stats_enabled = bool(
                getattr(db, "_turbo_update_stats_enabled", True)
            )
            db.set_turbo_update_stats_enabled(False)
        except Exception:
            prev_turbo_update_stats_enabled = True
    default_max_workers = _default_laptop_max_workers()
    try:
        max_workers = max(
            1,
            int(os.environ.get("SARI_INDEXER_MAX_WORKERS", str(default_max_workers)) or str(default_max_workers)),
        )
    except Exception:
        max_workers = default_max_workers
    executor: Optional[concurrent.futures.Executor] = None
    try:
        default_inflight = _default_laptop_max_inflight(max_workers)
        max_inflight = max(
            int(max_workers),
            int(os.environ.get("SARI_INDEXER_MAX_INFLIGHT", str(default_inflight)) or str(default_inflight)),
        )
    except Exception:
        max_inflight = _default_laptop_max_inflight(max_workers)
    try:
        flush_file_rows = max(1, int(os.environ.get("SARI_INDEXER_FLUSH_FILE_ROWS", "200") or 200))
    except Exception:
        flush_file_rows = 200
    try:
        flush_seen_rows = max(1, int(os.environ.get("SARI_INDEXER_FLUSH_SEEN_ROWS", "1000") or 1000))
    except Exception:
        flush_seen_rows = 1000
    try:
        flush_symbol_rows = max(1, int(os.environ.get("SARI_INDEXER_FLUSH_SYMBOL_ROWS", "2000") or 2000))
    except Exception:
        flush_symbol_rows = 2000
    try:
        flush_rel_rows = max(1, int(os.environ.get("SARI_INDEXER_FLUSH_REL_ROWS", "4000") or 4000))
    except Exception:
        flush_rel_rows = 4000
    try:
        flush_rel_replace_rows = max(
            1, int(os.environ.get("SARI_INDEXER_FLUSH_REL_REPLACE_ROWS", "2000") or 2000))
    except Exception:
        flush_rel_replace_rows = 2000
    check_parent_alive = parent_alive_check or _is_pid_alive
    progress_every_files = max(1, int(os.environ.get("SARI_INDEXER_PROGRESS_EVERY_FILES", "200") or 200))
    progress_every_sec = max(0.2, float(os.environ.get("SARI_INDEXER_PROGRESS_EVERY_SEC", "1.5") or 1.5))
    progress_log_enabled = str(os.environ.get("SARI_INDEXER_PROGRESS_LOG", "1")).strip().lower() in {
        "1", "true", "yes", "on"}
    adaptive_flush_enabled = str(
        os.environ.get("SARI_INDEXER_ADAPTIVE_FLUSH", "1")
    ).strip().lower() in {"1", "true", "yes", "on"}
    phase_mode = str(os.environ.get("SARI_INDEXER_PHASE_MODE", "full") or "full").strip().lower()
    extract_symbols_enabled = phase_mode != "fast"
    force_reparse_enabled = str(
        os.environ.get("SARI_INDEXER_FORCE_REPARSE", "0")
    ).strip().lower() in {"1", "true", "yes", "on"}
    initial_fastpath_enabled = str(
        os.environ.get("SARI_INDEXER_INITIAL_FASTPATH", "1")
    ).strip().lower() in {"1", "true", "yes", "on"}
    cpu_throttle_enabled = str(
        os.environ.get("SARI_INDEXER_CPU_THROTTLE_ENABLED", "1")
    ).strip().lower() in {"1", "true", "yes", "on"}
    try:
        cpu_high_watermark = float(
            os.environ.get("SARI_INDEXER_CPU_HIGH_WATERMARK", "70") or 70
        )
    except Exception:
        cpu_high_watermark = 70.0
    try:
        cpu_resume_watermark = float(
            os.environ.get("SARI_INDEXER_CPU_RESUME_WATERMARK", "55") or 55
        )
    except Exception:
        cpu_resume_watermark = 55.0
    if cpu_resume_watermark > cpu_high_watermark:
        cpu_resume_watermark = max(0.0, cpu_high_watermark - 5.0)
    try:
        cpu_sample_sec = max(
            0.2,
            float(os.environ.get("SARI_INDEXER_CPU_SAMPLE_SEC", "1.0") or 1.0),
        )
    except Exception:
        cpu_sample_sec = 1.0
    try:
        throttled_workers = max(
            1,
            int(os.environ.get("SARI_INDEXER_CPU_THROTTLED_WORKERS", "2") or 2),
        )
    except Exception:
        throttled_workers = 2
    incremental_low_impact_enabled = str(
        os.environ.get("SARI_INDEXER_INCREMENTAL_LOW_IMPACT", "1")
    ).strip().lower() in {"1", "true", "yes", "on"}
    try:
        max_buffer_bytes = max(
            1 << 20,
            int(os.environ.get("SARI_INDEXER_MAX_BUFFER_BYTES", str(64 * 1024 * 1024)) or str(64 * 1024 * 1024)),
        )
    except Exception:
        max_buffer_bytes = 64 * 1024 * 1024
    cpu_throttle_active = False
    last_cpu_sample_ts = 0.0
    sampled_cpu_percent: Optional[float] = None
    status["adaptive_flush_enabled"] = bool(adaptive_flush_enabled)
    last_progress_ts = time.time()
    flush_state: dict[str, int] = {
        "file_rows": int(flush_file_rows),
        "seen_rows": int(flush_seen_rows),
        "symbol_rows": int(flush_symbol_rows),
        "rel_rows": int(flush_rel_rows),
        "rel_replace_rows": int(flush_rel_replace_rows),
    }

    def _ensure_parent_alive() -> None:
        if parent_pid is None:
            return
        if not check_parent_alive(int(parent_pid)):
            raise RuntimeError(
                f"orphaned worker detected: parent pid {parent_pid} is not alive")

    def _emit_progress(*, force: bool = False, stage: str = "") -> None:
        nonlocal last_progress_ts
        now = time.time()
        should_emit = force
        if not should_emit:
            if int(status["scanned_files"] or 0) % progress_every_files == 0:
                should_emit = True
            elif (now - last_progress_ts) >= progress_every_sec:
                should_emit = True
        if not should_emit:
            return
        last_progress_ts = now
        payload = dict(status)
        payload["stage"] = str(stage or "")
        payload["pending_inflight"] = int(len(pending)) if "pending" in locals() else 0
        payload["max_inflight"] = int(max_inflight)
        payload["cpu_throttle_active"] = bool(cpu_throttle_active)
        payload["cpu_percent"] = sampled_cpu_percent
        payload["flush_thresholds"] = dict(flush_state)
        if progress_callback is not None:
            try:
                progress_callback(payload)
            except Exception:
                pass
        if logger and progress_log_enabled:
            logger.info(
                "indexer_worker_progress stage=%s scanned=%s indexed=%s symbols=%s errors=%s",
                stage or "running",
                int(status.get("scanned_files", 0) or 0),
                int(status.get("indexed_files", 0) or 0),
                int(status.get("symbols_extracted", 0) or 0),
                int(status.get("errors", 0) or 0),
            )

    try:
        _emit_progress(force=True, stage="start")
        def _get_files_generator():
            """
            모든 파일을 메모리에 로드하지 않고 하나씩 처리하기 위한 제너레이터입니다.
            각 루트 디렉토리를 순회하며 인덱싱 대상 파일을 선별합니다.
            """
            scanner = Scanner(config, active_workspaces=[str(r) for r in config.workspace_roots])
            for root in config.workspace_roots:
                rid = WorkspaceManager.root_id(root)
                db.ensure_root(rid, str(root))
                root_path = Path(root)
                for path, st, _excluded in scanner.iter_file_entries(root_path, apply_exclude=True):
                    if config.should_index(str(path)):
                        yield root, path, rid, st

        pending: set[concurrent.futures.Future] = set()
        now = int(time.time())
        root_initial_empty: dict[str, bool] = {}
        if initial_fastpath_enabled:
            for root in config.workspace_roots:
                rid = WorkspaceManager.root_id(str(root))
                try:
                    row = db.execute(
                        "SELECT 1 FROM files WHERE root_id = ? AND deleted_ts = 0 LIMIT 1",
                        (rid,),
                    ).fetchone()
                    root_initial_empty[rid] = row is None
                except Exception:
                    root_initial_empty[rid] = False
        initial_all_empty = bool(root_initial_empty) and all(
            bool(v) for v in root_initial_empty.values()
        )
        max_workers, max_inflight = _apply_incremental_low_impact_caps(
            max_workers,
            max_inflight,
            incremental_mode=not initial_all_empty,
            enabled=incremental_low_impact_enabled,
        )
        use_process_pool = False
        global _PROCESS_POOL_ALLOWED
        if initial_fastpath_enabled and root_initial_empty:
            try:
                process_pool_enabled = str(
                    os.environ.get("SARI_INDEXER_INITIAL_PROCESS_POOL", "1")
                ).strip().lower() in {"1", "true", "yes", "on"}
            except Exception:
                process_pool_enabled = False
            use_process_pool = bool(
                process_pool_enabled
                and initial_all_empty
                and not force_reparse_enabled
            )
            # Keep UI responsive when system is already busy.
            if cpu_throttle_enabled:
                cpu_now = _read_system_cpu_percent()
                if cpu_now is not None and cpu_now >= cpu_high_watermark:
                    use_process_pool = False
            if _PROCESS_POOL_ALLOWED is False:
                use_process_pool = False
        if use_process_pool:
            cfg_payload = config.model_dump(mode="python") if hasattr(config, "model_dump") else dict(config.__dict__)
            try:
                executor = concurrent.futures.ProcessPoolExecutor(
                    max_workers=max_workers,
                    initializer=init_process_worker,
                    initargs=(cfg_payload,),
                )
                _PROCESS_POOL_ALLOWED = True
            except Exception as e:
                use_process_pool = False
                _PROCESS_POOL_ALLOWED = False
                if logger:
                    logger.warning("initial process pool unavailable; fallback to thread pool: %s", e)
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        else:
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        file_rows = []
        seen_paths = []
        all_symbols = []
        all_relations = []
        relation_replace_sources: set[tuple[str, str]] = set()
        file_buffer_bytes = 0
        symbol_buffer_bytes = 0
        relation_buffer_bytes = 0

        def _flush_batches(force: bool = False) -> None:
            nonlocal file_buffer_bytes, symbol_buffer_bytes, relation_buffer_bytes
            file_rows_threshold = _adaptive_flush_threshold(
                flush_file_rows,
                pending_count=len(pending),
                max_inflight=max_inflight,
                enabled=adaptive_flush_enabled,
            )
            symbol_rows_threshold = _adaptive_flush_threshold(
                flush_symbol_rows,
                pending_count=len(pending),
                max_inflight=max_inflight,
                enabled=adaptive_flush_enabled,
            )
            rel_rows_threshold = _adaptive_flush_threshold(
                flush_rel_rows,
                pending_count=len(pending),
                max_inflight=max_inflight,
                enabled=adaptive_flush_enabled,
            )
            rel_replace_threshold = _adaptive_flush_threshold(
                flush_rel_replace_rows,
                pending_count=len(pending),
                max_inflight=max_inflight,
                enabled=adaptive_flush_enabled,
            )
            flush_state["file_rows"] = int(file_rows_threshold)
            flush_state["symbol_rows"] = int(symbol_rows_threshold)
            flush_state["rel_rows"] = int(rel_rows_threshold)
            flush_state["rel_replace_rows"] = int(rel_replace_threshold)
            files_flushed = False
            if file_rows and (force or len(file_rows) >= file_rows_threshold):
                rows = list(file_rows)
                file_rows.clear()
                file_buffer_bytes = 0
                db.upsert_files_turbo(rows)
                db.finalize_turbo_batch()
                files_flushed = True
            # Symbols reference files(path) via FK; when symbol flush is due,
            # ensure pending file rows are committed first.
            if (
                file_rows
                and not files_flushed
                and all_symbols
                and (force or len(all_symbols) >= symbol_rows_threshold)
            ):
                rows = list(file_rows)
                file_rows.clear()
                file_buffer_bytes = 0
                db.upsert_files_turbo(rows)
                db.finalize_turbo_batch()
                files_flushed = True
            if all_symbols and (force or len(all_symbols) >= symbol_rows_threshold):
                rows = list(all_symbols)
                all_symbols.clear()
                symbol_buffer_bytes = 0
                try:
                    db.upsert_symbols_tx(None, rows)
                except Exception as e:
                    if logger:
                        logger.error(f"Failed to store extracted symbols: {e}")
            if all_relations and (force or len(all_relations) >= rel_rows_threshold):
                rows = list(all_relations)
                all_relations.clear()
                relation_buffer_bytes = 0
                replace_sources = list(relation_replace_sources)
                relation_replace_sources.clear()
                try:
                    db.upsert_relations_tx(
                        None,
                        rows,
                        replace_sources=replace_sources,
                    )
                except Exception as e:
                    if logger:
                        logger.error(f"Failed to store extracted relations: {e}")
            elif relation_replace_sources and force:
                replace_sources = list(relation_replace_sources)
                relation_replace_sources.clear()
                try:
                    db.upsert_relations_tx(None, [], replace_sources=replace_sources)
                except Exception as e:
                    if logger:
                        logger.error(f"Failed to replace extracted relations: {e}")
            elif relation_replace_sources and len(relation_replace_sources) >= rel_replace_threshold:
                replace_sources = list(relation_replace_sources)
                relation_replace_sources.clear()
                try:
                    db.upsert_relations_tx(None, [], replace_sources=replace_sources)
                except Exception as e:
                    if logger:
                        logger.error(f"Failed to replace extracted relations: {e}")

        def _flush_seen_paths(force: bool = False) -> None:
            if not seen_paths:
                return
            seen_threshold = _adaptive_flush_threshold(
                flush_seen_rows,
                pending_count=len(pending),
                max_inflight=max_inflight,
                enabled=adaptive_flush_enabled,
            )
            flush_state["seen_rows"] = int(seen_threshold)
            if not force and len(seen_paths) < seen_threshold:
                return
            rows = list(OrderedDict.fromkeys(seen_paths))
            seen_paths.clear()
            try:
                db.update_last_seen_tx(None, rows, now)
            except Exception as e:
                if logger:
                    logger.error(f"Failed to refresh last_seen_ts for scanned paths: {e}")

        def _consume_future_result(future: concurrent.futures.Future) -> None:
            nonlocal file_buffer_bytes, symbol_buffer_bytes, relation_buffer_bytes
            _ensure_parent_alive()
            try:
                res_obj: Optional[IndexingResult | dict[str, object]] = future.result()
                if isinstance(res_obj, dict):
                    res = IndexingResult(**res_obj)
                else:
                    res = res_obj
                if not res:
                    return

                if res.type in ("changed", "new"):
                    status["indexed_files"] += 1
                    file_rows.append(res.to_file_row())
                    try:
                        file_buffer_bytes += int(getattr(res, "content_bytes", 0) or 0) + 256
                    except Exception:
                        file_buffer_bytes += 256

                    root_id = res.root_id
                    if not bool(root_initial_empty.get(root_id, False)):
                        relation_replace_sources.add((res.path, root_id))
                    if res.symbols:
                        for s in res.symbols:
                            all_symbols.append(
                                (s.sid,
                                 s.path,
                                 root_id,
                                 s.name,
                                 s.kind,
                                 s.line,
                                 s.end_line,
                                 s.content,
                                 s.parent,
                                 "{}" if not s.meta else json.dumps(s.meta),
                                    s.doc,
                                    s.qualname))
                            symbol_buffer_bytes += len(getattr(s, "content", "") or "") + len(getattr(s, "name", "") or "") + 64
                        status["symbols_extracted"] += len(res.symbols)
                    elif not extract_symbols_enabled:
                        status["symbols_deferred_files"] += 1

                    if res.relations:
                        for r in res.relations:
                            all_relations.append(
                                (res.path,
                                 root_id,
                                 r.from_name,
                                 r.from_sid,
                                 r.to_path or res.path,
                                 root_id,
                                 r.to_name,
                                 r.to_sid,
                                 r.rel_type,
                                 r.line,
                                 "{}" if not r.meta else json.dumps(r.meta)))
                            relation_buffer_bytes += (
                                len(getattr(r, "from_name", "") or "")
                                + len(getattr(r, "to_name", "") or "")
                                + len(getattr(r, "rel_type", "") or "")
                                + 96
                            )

            except Exception as e:
                status["errors"] += 1
                if logger:
                    logger.error(f"Async result processing failed: {e}")
                _emit_progress(stage="collect_error")
            else:
                _emit_progress(stage="collect")
                buffered_total = int(file_buffer_bytes) + int(symbol_buffer_bytes) + int(relation_buffer_bytes)
                _flush_batches(force=buffered_total >= int(max_buffer_bytes))

        def _drain_pending(wait_one: bool) -> None:
            if not pending:
                return
            if wait_one:
                done, not_done = concurrent.futures.wait(
                    pending,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
            else:
                done, not_done = concurrent.futures.wait(pending)
            pending.clear()
            pending.update(not_done)
            for fut in done:
                _consume_future_result(fut)

        for root, path, rid, st in _get_files_generator():
            _ensure_parent_alive()
            if cpu_throttle_enabled:
                ts_now = time.time()
                if (ts_now - last_cpu_sample_ts) >= cpu_sample_sec:
                    last_cpu_sample_ts = ts_now
                    cpu_now = _read_system_cpu_percent()
                    if cpu_now is not None:
                        sampled_cpu_percent = float(cpu_now)
                        cpu_throttle_active = _update_cpu_throttle_state(
                            cpu_throttle_active,
                            sampled_cpu_percent,
                            high_watermark=cpu_high_watermark,
                            resume_watermark=cpu_resume_watermark,
                        )
            status["scanned_files"] += 1
            rel_to_root = PathUtils.to_relative(str(path), str(root)) or path.name
            seen_paths.append(f"{rid}/{rel_to_root}")
            _flush_seen_paths(force=False)
            _emit_progress(stage="enqueue")
            try:
                # 파일 처리 작업을 실행기에 제출
                skip_prev_lookup = bool(root_initial_empty.get(rid, False))
                if use_process_pool:
                    fut = executor.submit(
                        process_file_task_in_process,
                        (
                            str(root),
                            str(path),
                            int(getattr(st, "st_mtime", 0) or 0),
                            int(getattr(st, "st_size", 0) or 0),
                            int(now),
                            float(getattr(st, "st_mtime", 0.0) or 0.0),
                            str(rid),
                            bool(extract_symbols_enabled),
                        ),
                    )
                elif force_reparse_enabled:
                    if _worker_accepts_skip_prev_lookup:
                        fut = executor.submit(
                            worker.process_file_task,
                            root,
                            path,
                            st,
                            now,
                            st.st_mtime,
                            True,
                            root_id=rid,
                            force=True,
                            skip_prev_lookup=skip_prev_lookup,
                            extract_symbols=extract_symbols_enabled,
                        )
                    else:
                        fut = executor.submit(
                            worker.process_file_task,
                            root,
                            path,
                            st,
                            now,
                            st.st_mtime,
                            True,
                            root_id=rid,
                            force=True,
                            extract_symbols=extract_symbols_enabled,
                        )
                else:
                    if skip_prev_lookup:
                        if _worker_accepts_skip_prev_lookup:
                            fut = executor.submit(
                                worker.process_file_task,
                                root,
                                path,
                                st,
                                now,
                                st.st_mtime,
                                True,
                                root_id=rid,
                                skip_prev_lookup=True,
                                extract_symbols=extract_symbols_enabled,
                            )
                        else:
                            fut = executor.submit(
                                worker.process_file_task,
                                root,
                                path,
                                st,
                                now,
                                st.st_mtime,
                                True,
                                root_id=rid,
                                extract_symbols=extract_symbols_enabled,
                            )
                    else:
                        fut = executor.submit(
                            worker.process_file_task,
                            root,
                            path,
                            st,
                            now,
                            st.st_mtime,
                            True,
                            root_id=rid,
                            extract_symbols=extract_symbols_enabled,
                        )
                pending.add(fut)
                effective_inflight = _effective_inflight_limit(
                    max_inflight,
                    max_workers,
                    throttle_active=cpu_throttle_active and not use_process_pool,
                    throttle_workers=throttled_workers,
                )
                if len(pending) >= effective_inflight:
                    _drain_pending(wait_one=True)
            except Exception:
                status["errors"] += 1

        # 완료된 작업 결과 수집
        _drain_pending(wait_one=False)

        # 데이터베이스 일괄 업데이트 (Batch Update)
        _flush_seen_paths(force=True)
        _flush_batches(force=True)
        # 현재 스캔에서 관측되지 않은 과거 row는 soft-delete 처리한다.
        # exclude 정책 변경 시 기존 노이즈(.venv/.idea 등)가 검색에 남지 않도록 보장한다.
        try:
            scanned_root_ids = []
            for root in config.workspace_roots:
                try:
                    scanned_root_ids.append(WorkspaceManager.root_id(str(root)))
                except Exception:
                    continue
            scanned_root_ids = [rid for rid in scanned_root_ids if rid]
            if scanned_root_ids:
                _cleanup_deleted_paths(
                    db,
                    scanned_root_ids,
                    now_ts=now,
                    logger=logger,
                )
        except Exception:
            if logger:
                logger.debug("legacy-row soft-delete cleanup skipped", exc_info=True)

        status["scan_finished_ts"] = int(time.time())
        status["index_version"] = str(status["scan_finished_ts"])
        _emit_progress(force=True, stage="done")
        return status
    finally:
        if hasattr(db, "set_turbo_update_stats_enabled"):
            try:
                db.set_turbo_update_stats_enabled(prev_turbo_update_stats_enabled)
            except Exception:
                pass
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


def _worker_build_snapshot(
        config_dict: dict[str, object],
        snapshot_path: str,
        status_path: str,
        log_path: str,
        parent_pid: Optional[int] = None) -> None:
    """별도 프로세스에서 인덱싱을 수행하고 결과를 스냅샷 DB 및 상태 파일에 기록합니다."""
    logger = logging.getLogger("sari.indexer.worker")
    if log_path:
        try:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(fh)
            logger.setLevel(logging.INFO)
        except Exception:
            pass
    try:
        def _write_worker_status(payload: dict[str, object]) -> None:
            tmp_path = f"{status_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_path, status_path)

        def _progress_callback(status_payload: dict[str, object]) -> None:
            _write_worker_status(
                {
                    "ok": True,
                    "in_progress": True,
                    "status": status_payload,
                    "snapshot_path": snapshot_path,
                }
            )

        cfg = Config(**config_dict)
        db = LocalSearchDB(snapshot_path, logger=logger, bind_proxy=False, journal_mode="delete")
        status = _scan_to_db(
            cfg,
            db,
            logger,
            parent_pid=parent_pid,
            progress_callback=_progress_callback,
        )
        db.close_all()
        # 성공 상태 기록
        _write_worker_status(
            {
                "ok": True,
                "in_progress": False,
                "status": status,
                "snapshot_path": snapshot_path,
            }
        )
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Worker snapshot build failed: %s", e)
        logger.debug("Worker snapshot traceback:\n%s", tb)
        # 실패 상태 기록
        try:
            tmp_path = f"{status_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "ok": False,
                        "in_progress": False,
                        "error": str(e),
                        "traceback": tb,
                        "snapshot_path": snapshot_path,
                    },
                    f,
                )
            os.replace(tmp_path, status_path)
        except Exception:
            pass


class Indexer:
    """
    전체 인덱싱 작업을 관리하는 클래스입니다.
    주기적인 스캔, 온디맨드 리스캔, 워커 프로세스 관리 등을 담당합니다.
    """

    def __init__(
            self,
            config: Config,
            db: LocalSearchDB,
            logger=None,
            **kwargs):
        self.config = config
        self.db = db
        self.logger = logger or logging.getLogger("sari.indexer")
        self.status = IndexStatus()
        self.worker = IndexWorker(config, db, self.logger, None)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.indexing_enabled = True
        self.indexer_mode = "worker"
        self._rescan_event = threading.Event()
        self._stop_event = threading.Event()
        self._scan_lock = threading.Lock()
        self._worker_proc: Optional[multiprocessing.Process] = None
        self._worker_snapshot_path: Optional[str] = None
        self._worker_status_path: Optional[str] = None
        self._worker_log_path: Optional[str] = None
        self._pending_rescan = False

    def _remove_file_if_exists(self, path: Optional[str]) -> None:
        if not path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            if self.logger:
                self.logger.debug("Failed to remove file %s: %s", path, e)

    def _cleanup_snapshot_artifacts(
            self, snapshot_path: Optional[str]) -> None:
        if not snapshot_path:
            return
        self._remove_file_if_exists(snapshot_path)
        self._remove_file_if_exists(f"{snapshot_path}-wal")
        self._remove_file_if_exists(f"{snapshot_path}-shm")
        self._remove_file_if_exists(f"{snapshot_path}-journal")

    def _read_worker_log_tail(
            self, path: Optional[str], max_chars: int = 1200) -> str:
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if len(content) <= max_chars:
                return content
            return content[-max_chars:]
        except Exception:
            return ""

    def _read_worker_progress_status(self) -> Optional[dict[str, object]]:
        status_path = self._worker_status_path
        if not status_path or not os.path.exists(status_path):
            return None
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        if not payload.get("ok", False):
            return None
        status_payload = payload.get("status", {})
        if not isinstance(status_payload, dict):
            return None
        return status_payload

    def get_runtime_status(self) -> dict[str, object]:
        status_obj = self.status
        runtime = {
            "index_ready": bool(getattr(status_obj, "index_ready", False)),
            "indexed_files": int(getattr(status_obj, "indexed_files", 0) or 0),
            "scanned_files": int(getattr(status_obj, "scanned_files", 0) or 0),
            "symbols_extracted": int(getattr(status_obj, "symbols_extracted", 0) or 0),
            "errors": int(getattr(status_obj, "errors", 0) or 0),
            "scan_started_ts": int(getattr(status_obj, "scan_started_ts", 0) or 0),
            "scan_finished_ts": int(getattr(status_obj, "scan_finished_ts", 0) or 0),
            "index_version": str(getattr(status_obj, "index_version", "") or ""),
            "last_error": str(getattr(status_obj, "last_error", "") or ""),
            "status_source": "indexer_status",
        }
        worker_proc = self._worker_proc
        worker_alive = bool(worker_proc and worker_proc.is_alive())
        if not worker_alive:
            return runtime
        progress = self._read_worker_progress_status() or {}
        if progress:
            runtime["index_ready"] = bool(progress.get("index_ready", False))
            runtime["indexed_files"] = int(progress.get("indexed_files", 0) or 0)
            runtime["scanned_files"] = int(progress.get("scanned_files", 0) or 0)
            runtime["symbols_extracted"] = int(progress.get("symbols_extracted", 0) or 0)
            runtime["errors"] = int(progress.get("errors", 0) or 0)
            runtime["scan_started_ts"] = int(progress.get("scan_started_ts", runtime["scan_started_ts"]) or 0)
            runtime["scan_finished_ts"] = int(progress.get("scan_finished_ts", runtime["scan_finished_ts"]) or 0)
            runtime["index_version"] = str(progress.get("index_version", runtime["index_version"]) or "")
            runtime["status_source"] = "worker_progress"
        else:
            runtime["index_ready"] = False
            runtime["status_source"] = "worker_pending"
        return runtime

    def _cleanup_stale_snapshot_artifacts(
            self,
            now_ts: Optional[int] = None,
            max_age_seconds: int = 3600) -> None:
        base = self._safe_db_path()
        if not base:
            return
        parent = os.path.dirname(base) or "."
        prefix = f"{os.path.basename(base)}.snapshot."
        now = int(now_ts if now_ts is not None else time.time())
        try:
            for name in os.listdir(parent):
                if not name.startswith(prefix):
                    continue
                path = os.path.join(parent, name)
                try:
                    st = os.stat(path)
                except Exception:
                    continue
                if (now - int(st.st_mtime)) >= int(max_age_seconds):
                    self._remove_file_if_exists(path)
        except Exception as e:
            if self.logger:
                self.logger.debug(
                    "Failed to cleanup stale snapshots under %s: %s", parent, e)

    def scan_once(self):
        """동기적으로 1회 스캔을 수행합니다. (블로킹)"""
        with self._scan_lock:
            self.status.index_ready = False
            snapshot_path = self._snapshot_path()
            snapshot_db = LocalSearchDB(
                snapshot_path,
                logger=self.logger,
                bind_proxy=False,
                journal_mode="delete")
            status = _scan_to_db(self.config, snapshot_db, self.logger)
            try:
                snapshot_db.close_all()
            except Exception:
                try:
                    snapshot_db.close()
                except Exception:
                    pass
            try:
                # 스냅샷 DB를 메인 DB로 교체 (Swap)
                self.db.swap_db_file(snapshot_path)
                self.status.scan_started_ts = status.get("scan_started_ts", 0)
                self.status.scan_finished_ts = status.get(
                    "scan_finished_ts", 0)
                self.status.scanned_files = status.get("scanned_files", 0)
                self.status.indexed_files = status.get("indexed_files", 0)
                self.status.symbols_extracted = status.get(
                    "symbols_extracted", 0)
                self.status.errors = status.get("errors", 0)
                self.status.index_version = status.get("index_version", "")
                self.status.index_ready = True
                self._cleanup_snapshot_artifacts(snapshot_path)
            except Exception as e:
                self.status.errors += 1
                self.status.last_error = str(e)
                if self.logger:
                    self.logger.error(f"Snapshot swap failed: {e}")

    def stop(self):
        """인덱서 실행을 중단하고 리소스를 정리합니다."""
        self._stop_event.set()
        if self._worker_proc and self._worker_proc.is_alive():
            try:
                self._worker_proc.terminate()
                self._worker_proc.join(timeout=3.0)
                if self._worker_proc.is_alive():
                    self._worker_proc.kill()
                    self._worker_proc.join(timeout=1.0)
            except Exception:
                pass
        self._cleanup_snapshot_artifacts(self._worker_snapshot_path)
        self._remove_file_if_exists(self._worker_status_path)
        self._remove_file_if_exists(self._worker_log_path)
        self._worker_proc = None
        self._worker_snapshot_path = None
        self._worker_status_path = None
        self._worker_log_path = None
        self._cleanup_stale_snapshot_artifacts()
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

    def run_forever(self):
        """백그라운드에서 주기적으로 인덱싱 작업을 수행하는 무한 루프입니다."""
        next_due = time.time()
        while not self._stop_event.is_set():
            self._finalize_worker_if_done()
            now = time.time()
            if self._rescan_event.is_set() or now >= next_due:
                self._rescan_event.clear()
                self._start_worker_scan()
                next_due = now + self.config.scan_interval_seconds
            time.sleep(0.2)

    def request_rescan(self):
        """즉시 리스캔을 요청합니다."""
        self._rescan_event.set()

    def index_file(self, _path: str):
        """특정 파일 변경 시 인덱싱을 요청합니다."""
        self.request_rescan()

    def _enqueue_fsevent(self, _evt: object) -> None:
        """파일 시스템 이벤트를 처리합니다."""
        self.request_rescan()

    def _snapshot_path(self) -> str:
        """임시 스냅샷 DB 파일 경로를 생성합니다."""
        import random
        base = self._safe_db_path()
        if base in ("", ":memory:"):
            tmp_dir = os.path.join(tempfile.gettempdir(), "sari_snapshots")
            os.makedirs(tmp_dir, exist_ok=True)
            base = os.path.join(tmp_dir, "index.db")
        # Add PID and a random suffix to prevent collisions within the same millisecond
        pid = os.getpid()
        rand = random.randint(1000, 9999)
        return f"{base}.snapshot.{int(time.time() * 1000)}.{pid}.{rand}"

    def _safe_db_path(self) -> str:
        """db_path를 안전한 문자열 경로로 정규화합니다."""
        raw = getattr(self.db, "db_path", "")
        if isinstance(raw, str):
            return raw
        if isinstance(raw, Path):
            return str(raw)
        return ""

    def _serialize_config(self) -> dict[str, object]:
        """설정 객체를 직렬화하여 워커 프로세스에 전달합니다. 직렬화 불가능한 필드는 제외합니다."""
        data = {}
        for k, v in self.config.__dict__.items():
            # Skip non-serializable or internal objects
            if k.startswith("_") or callable(v) or "lock" in k.lower() or "logger" in k.lower():
                continue
            # Ensure path objects are strings
            if hasattr(v, "__fspath__"):
                data[k] = str(v)
            else:
                data[k] = v
        return data

    def _start_worker_scan(self) -> None:
        """별도 프로세스에서 인덱싱 작업을 시작합니다."""
        if self._worker_proc and self._worker_proc.is_alive():
            self._pending_rescan = True
            if self.logger:
                self.logger.warning(
                    "Indexer worker busy; rescan queued (pid=%s)",
                    getattr(self._worker_proc, "pid", None),
                )
            return
        self._cleanup_stale_snapshot_artifacts()
        self.status.index_ready = False
        self.status.last_error = ""
        self._worker_snapshot_path = self._snapshot_path()
        token = f"{int(time.time() * 1000)}.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        self._worker_status_path = f"{self.db.db_path}.snapshot.{token}.status.json"
        self._worker_log_path = f"{self.db.db_path}.snapshot.{token}.log"
        cfg = self._serialize_config()
        ctx = multiprocessing.get_context("spawn")
        self._worker_proc = ctx.Process(
            target=_worker_build_snapshot,
            args=(
                cfg,
                self._worker_snapshot_path,
                self._worker_status_path,
                self._worker_log_path,
                os.getpid()),
            daemon=True)
        self._worker_proc.start()
        if self.logger:
            self.logger.info(
                "Started indexer worker (pid=%s, snapshot=%s, status=%s, log=%s)",
                getattr(self._worker_proc, "pid", None),
                self._worker_snapshot_path,
                self._worker_status_path,
                self._worker_log_path,
            )

    def _finalize_worker_if_done(self) -> None:
        """워커 프로세스가 완료되었는지 확인하고 결과를 반영합니다."""
        if not self._worker_proc:
            return
        if self._worker_proc.is_alive():
            return
        exitcode = self._worker_proc.exitcode
        self._worker_proc = None
        status_path = self._worker_status_path
        log_path = self._worker_log_path
        snapshot_path = self._worker_snapshot_path
        self._worker_status_path = None
        self._worker_log_path = None
        self._worker_snapshot_path = None
        if not status_path or not snapshot_path or not os.path.exists(
                status_path):
            self.status.errors += 1
            self.status.last_error = "worker status missing"
            if self.logger:
                log_tail = self._read_worker_log_tail(log_path)
                self.logger.error(
                    "Indexer worker failed: status file missing "
                    "(exitcode=%s, status_path=%s, snapshot=%s, log_path=%s, log_tail=%r)",
                    exitcode,
                    status_path,
                    snapshot_path,
                    log_path,
                    log_tail,
                )
            self._cleanup_snapshot_artifacts(snapshot_path)
            self._remove_file_if_exists(log_path)
            return
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            self.status.errors += 1
            self.status.last_error = f"worker status read failed: {e}"
            if self.logger:
                self.logger.error(
                    "Indexer worker status read failed "
                    "(status_path=%s, snapshot=%s, log_path=%s): %s",
                    status_path,
                    snapshot_path,
                    log_path,
                    e,
                )
            self._cleanup_snapshot_artifacts(snapshot_path)
            self._remove_file_if_exists(status_path)
            self._remove_file_if_exists(log_path)
            return
        if not payload.get("ok"):
            self.status.errors += 1
            self.status.last_error = payload.get("error", "worker failed")
            if self.logger:
                self.logger.error(
                    "Indexer worker reported failure "
                    "(status_path=%s, snapshot=%s, log_path=%s, error=%s, traceback=%s)",
                    status_path,
                    snapshot_path,
                    log_path,
                    payload.get("error", "worker failed"),
                    payload.get("traceback", ""),
                )
            self._cleanup_snapshot_artifacts(snapshot_path)
            self._remove_file_if_exists(status_path)
            self._remove_file_if_exists(log_path)
            return
        if exitcode not in (0, None):
            self.status.errors += 1
            self.status.last_error = f"worker exit {exitcode}"
            if self.logger:
                log_tail = self._read_worker_log_tail(log_path)
                self.logger.error(
                    "Indexer worker exited with non-zero code "
                    "(exitcode=%s, status_path=%s, snapshot=%s, log_path=%s, log_tail=%r)",
                    exitcode,
                    status_path,
                    snapshot_path,
                    log_path,
                    log_tail,
                )
            self._cleanup_snapshot_artifacts(snapshot_path)
            self._remove_file_if_exists(status_path)
            self._remove_file_if_exists(log_path)
            return
        status = payload.get("status", {})
        try:
            self.db.swap_db_file(snapshot_path)
            self.status.scan_started_ts = status.get("scan_started_ts", 0)
            self.status.scan_finished_ts = status.get("scan_finished_ts", 0)
            self.status.scanned_files = status.get("scanned_files", 0)
            self.status.indexed_files = status.get("indexed_files", 0)
            self.status.symbols_extracted = status.get("symbols_extracted", 0)
            self.status.errors = status.get("errors", 0)
            self.status.index_version = status.get("index_version", "")
            self.status.index_ready = True
            self._cleanup_snapshot_artifacts(snapshot_path)
        except Exception as e:
            self.status.errors += 1
            self.status.last_error = str(e)
            if self.logger:
                self.logger.error(f"Snapshot swap failed: {e}")
        finally:
            self._remove_file_if_exists(status_path)
            self._remove_file_if_exists(log_path)
        if self._pending_rescan:
            self._pending_rescan = False
            self._start_worker_scan()


class IndexStatus:
    """인덱싱 상태 정보를 저장하는 데이터 클래스입니다."""

    def __init__(self):
        self.index_ready = False
        self.indexed_files = 0
        self.symbols_extracted = 0
        self.scan_started_ts = 0
        self.scan_finished_ts = 0
        self.scanned_files = 0
        self.errors = 0
        self.index_version = ""
        self.last_error = ""

    def to_meta(self) -> dict:
        """상태 정보를 딕셔너리로 변환하여 반환합니다."""
        return {
            "index_ready": bool(self.index_ready),
            "indexed_files": int(self.indexed_files or 0),
            "scanned_files": int(self.scanned_files or 0),
            "index_errors": int(self.errors or 0),
            "symbols_extracted": int(self.symbols_extracted or 0),
            "index_version": self.index_version or "",
            "last_error": self.last_error or "",
            "scan_started_ts": int(self.scan_started_ts or 0),
            "scan_finished_ts": int(self.scan_finished_ts or 0),
        }
