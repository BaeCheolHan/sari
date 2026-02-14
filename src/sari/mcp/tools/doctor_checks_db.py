"""Database and engine related doctor checks."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TypeAlias

from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.settings import settings
from sari.core.workspace import WorkspaceManager
from sari.mcp.tools.doctor_common import compact_error_message, result, row_get, safe_pragma_table_name

DoctorResult: TypeAlias = dict[str, object]
DoctorResults: TypeAlias = list[DoctorResult]


def check_db(ws_root: str, *, allow_config_autofix: bool = False) -> DoctorResults:
    results: DoctorResults = []
    cfg_path = WorkspaceManager.resolve_config_path(ws_root)
    cfg = Config.load(cfg_path, workspace_root_override=ws_root)
    try:
        if cfg_path and Path(cfg_path).exists():
            raw = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict) and not raw.get("db_path") and cfg.db_path:
                if allow_config_autofix:
                    raw["db_path"] = cfg.db_path
                    Path(cfg_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(cfg_path).write_text(
                        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    results.append(result("DB Path AutoFix", True, f"db_path set to {cfg.db_path}"))
                else:
                    results.append(result("DB Path AutoFix", True, "skipped (auto_fix=false)", warn=True))
    except Exception as e:
        results.append(result("DB Path AutoFix", False, f"failed: {compact_error_message(e)}"))
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        results.append(result("DB Existence", False, f"DB not found at {db_path}"))
        return results

    try:
        db = LocalSearchDB(str(db_path))
    except Exception as e:
        results.append(result("DB Access", False, compact_error_message(e, "db access failed")))
        return results

    fts_ok = False
    try:
        cursor = db.db.connection().execute("PRAGMA compile_options")
        options = [str(row_get(r, "compile_options", 0, "") or "") for r in cursor.fetchall()]
        fts_ok = "ENABLE_FTS5" in options
    except Exception:
        fts_ok = False

    results.append(result("DB FTS5 Support", fts_ok, "FTS5 module missing in SQLite" if not fts_ok else ""))
    try:
        def _cols(table: str) -> list[str]:
            safe_name = safe_pragma_table_name(table)
            row = db.db.connection().execute(f"PRAGMA table_info({safe_name})")
            return [str(row_get(r, "name", 1, "") or "") for r in row.fetchall()]

        symbols_cols = _cols("symbols")
        if "end_line" in symbols_cols:
            results.append(result("DB Schema", True))
        else:
            results.append(result("DB Schema", False, "Column 'end_line' missing in 'symbols'. Run update."))
        if "qualname" in symbols_cols and "symbol_id" in symbols_cols:
            results.append(result("DB Schema Symbol IDs", True))
        else:
            results.append(result("DB Schema Symbol IDs", False, "Missing qualname/symbol_id in 'symbols'."))
        rel_cols = _cols("symbol_relations")
        if "from_symbol_id" in rel_cols and "to_symbol_id" in rel_cols:
            results.append(result("DB Schema Relations IDs", True))
        else:
            results.append(
                result("DB Schema Relations IDs", False, "Missing from_symbol_id/to_symbol_id in 'symbol_relations'.")
            )
        snippet_cols = _cols("snippets")
        if "anchor_before" in snippet_cols and "anchor_after" in snippet_cols:
            results.append(result("DB Schema Snippet Anchors", True))
        else:
            results.append(
                result("DB Schema Snippet Anchors", False, "Missing anchor_before/anchor_after in 'snippets'.")
            )
        ctx_cols = _cols("contexts")
        if all(c in ctx_cols for c in ("source", "valid_from", "valid_until", "deprecated")):
            results.append(result("DB Schema Context Validity", True))
        else:
            results.append(result("DB Schema Context Validity", False, "Missing validity columns in 'contexts'."))
        row = db.db.connection().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='snippet_versions'"
        ).fetchone()
        results.append(result("DB Schema Snippet Versions", bool(row), "snippet_versions table missing" if not row else ""))
        row = db.db.connection().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='failed_tasks'"
        ).fetchone()
        results.append(result("DB Schema Failed Tasks", bool(row), "failed_tasks table missing" if not row else ""))
        if row:
            total_failed, high_failed = db.count_failed_tasks()
            if high_failed >= 3:
                results.append(result("Failed Tasks (DLQ)", False, f"{high_failed} tasks exceeded retry threshold"))
            else:
                results.append(result("Failed Tasks (DLQ)", True, f"pending={total_failed}"))
    except Exception as e:
        results.append(result("DB Schema Check", False, compact_error_message(e, "db schema check failed")))
    finally:
        try:
            db.close()
        except Exception:
            pass

    return results


def check_db_migration_safety() -> DoctorResult:
    return result("DB Migration Safety", True, "using peewee init_schema (idempotent)")


def check_engine_sync_dlq(ws_root: str) -> DoctorResult:
    try:
        cfg_path = WorkspaceManager.resolve_config_path(ws_root)
        cfg = Config.load(cfg_path, workspace_root_override=ws_root)
        db = LocalSearchDB(cfg.db_path)
        try:
            row = db.db.connection().execute(
                "SELECT COUNT(*), COALESCE(MAX(attempts),0) FROM failed_tasks WHERE error LIKE 'engine_sync_error:%'"
            ).fetchone()
            count = int(row_get(row, "COUNT(*)", 0, 0) or 0)
            max_attempts = int(row_get(row, "COALESCE(MAX(attempts),0)", 1, 0) or 0)
            if count == 0:
                return result("Engine Sync DLQ", True, "no pending engine_sync_error tasks")
            return result("Engine Sync DLQ", False, f"pending={count} max_attempts={max_attempts}")
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception as e:
        return result("Engine Sync DLQ", False, compact_error_message(e, "engine sync dlq check failed"))


def check_writer_health(db: object = None) -> DoctorResult:
    try:
        from sari.core.db.storage import GlobalStorageManager

        sm = GlobalStorageManager.get_active_instance()
        if sm is None:
            return result("Writer Health", True, "no active storage manager")
        writer = getattr(sm, "writer", None)
        if writer is None:
            return result("Writer Health", False, "storage manager has no writer")
        thread_obj = getattr(writer, "_thread", None)
        alive = bool(thread_obj and thread_obj.is_alive())
        qsize = int(writer.qsize()) if hasattr(writer, "qsize") else -1
        last_commit = int(getattr(writer, "last_commit_ts", 0) or 0)
        age = int(time.time()) - last_commit if last_commit > 0 else -1
        if not alive:
            return result("Writer Health", False, f"writer thread not alive (queue={qsize})")
        if qsize > 1000:
            return result("Writer Health", True, f"writer alive but queue high={qsize}", warn=True)
        if qsize > 0 and age > 120:
            return result("Writer Health", True, f"writer alive with stale commits age_sec={age}", warn=True)
        detail = f"alive=true queue={qsize}"
        if age >= 0:
            detail += f" last_commit_age_sec={age}"
        return result("Writer Health", True, detail)
    except Exception as e:
        return result("Writer Health", False, compact_error_message(e, "writer health check failed"))


def check_storage_switch_guard() -> DoctorResult:
    try:
        from sari.core.db.storage import GlobalStorageManager

        reason, ts = GlobalStorageManager.get_switch_guard_status()
        if not reason:
            return result("Storage Switch Guard", True, "no blocked switch")
        age = int(time.time() - ts) if ts > 0 else -1
        msg = f"switch blocked: {reason}"
        if age >= 0:
            msg += f" age_sec={age}"
        return result("Storage Switch Guard", False, msg)
    except Exception as e:
        return result("Storage Switch Guard", False, compact_error_message(e, "storage switch guard check failed"))


def check_fts_rebuild_policy() -> DoctorResult:
    if settings.FTS_REBUILD_ON_START:
        return result(
            "FTS Rebuild Policy",
            True,
            "FTS_REBUILD_ON_START=true may increase startup latency",
            warn=True,
        )
    return result("FTS Rebuild Policy", True, "FTS_REBUILD_ON_START=false")


def check_engine_runtime(ws_root: str) -> DoctorResult:
    try:
        cfg_path = WorkspaceManager.resolve_config_path(ws_root)
        cfg = Config.load(cfg_path, workspace_root_override=ws_root)
        db_path = Path(str(cfg.db_path or "")).expanduser()
        try:
            max_db_mb = float(os.environ.get("SARI_DOCTOR_ENGINE_RUNTIME_MAX_DB_MB", "128") or "128")
        except Exception:
            max_db_mb = 128.0
        if max_db_mb > 0 and db_path.exists():
            db_size_mb = float(db_path.stat().st_size) / (1024.0 * 1024.0)
            if db_size_mb > max_db_mb:
                return result(
                    "Engine Runtime",
                    True,
                    f"skipped (db_size_mb={db_size_mb:.1f} > max_db_mb={max_db_mb:.1f})",
                    warn=True,
                )

        db = LocalSearchDB(cfg.db_path)
        try:
            from sari.core.settings import settings as _settings

            db.set_settings(_settings)
        except Exception:
            pass
        try:
            from sari.core.engine_registry import get_default_engine

            engine = get_default_engine(db, cfg, cfg.workspace_roots)
            db.set_engine(engine)
            if hasattr(engine, "status"):
                st = engine.status()
                mode = getattr(st, "engine_mode", "unknown")
                ready = bool(getattr(st, "engine_ready", False))
                reason = str(getattr(st, "reason", "") or "")
                hint = str(getattr(st, "hint", "") or "")
                detail = f"mode={mode} ready={str(ready).lower()}"
                if reason:
                    detail += f" reason={reason}"
                if hint:
                    detail += f" hint={hint}"
                passed = ready if mode == "embedded" else True
                return result("Search Engine Runtime", passed, detail)
            return result("Search Engine Runtime", False, "engine has no status()")
        finally:
            try:
                if hasattr(db, "engine") and hasattr(db.engine, "close"):
                    db.engine.close()
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass
    except Exception as e:
        return result("Search Engine Runtime", False, compact_error_message(e, "search engine runtime check failed"))


def check_db_integrity(ws_root: str) -> DoctorResult:
    try:
        cfg_path = WorkspaceManager.resolve_config_path(ws_root)
        cfg = Config.load(cfg_path, workspace_root_override=ws_root)
        db_path = Path(cfg.db_path)

        if not db_path.exists():
            return result("DB Integrity", False, "DB file missing")
        if db_path.stat().st_size == 0:
            return result("DB Integrity", False, "DB file is 0 bytes (empty)")

        import sqlite3

        mode = str(
            os.environ.get("SARI_DOCTOR_DB_INTEGRITY_MODE")
            or os.environ.get("SARI_DOCTOR_DEEP_DB_CHECK")
            or "light"
        ).strip().lower()
        if mode in {"1", "true", "yes", "on"}:
            mode = "deep"

        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            if mode not in {"quick", "deep"}:
                conn.execute("PRAGMA schema_version").fetchone()
                return result(
                    "DB Integrity",
                    True,
                    "SQLite connectivity ok (integrity scan skipped; set SARI_DOCTOR_DB_INTEGRITY_MODE=quick|deep)",
                    warn=True,
                )

            quick = conn.execute("PRAGMA quick_check(1)").fetchone()
            quick_res = str(next(iter(quick), "")) if quick else ""
            if quick_res != "ok":
                return result("DB Integrity", False, f"Corruption detected (quick_check): {quick_res}")

            if mode != "deep":
                return result("DB Integrity", True, "SQLite quick_check ok")

            cursor = conn.execute("PRAGMA integrity_check(10)")
            row = cursor.fetchone()
            res = str(next(iter(row), "")) if row else ""
            if res == "ok":
                return result("DB Integrity", True, "SQLite integrity_check ok")
            return result("DB Integrity", False, f"Corruption detected: {res}")
    except Exception as e:
        return result("DB Integrity", False, f"Check failed: {compact_error_message(e)}")
