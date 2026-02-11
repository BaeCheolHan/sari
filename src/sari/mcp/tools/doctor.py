#!/usr/bin/env python3
"""
로컬 검색 MCP 서버를 위한 진단 도구 (Doctor).
시스템 상태, 설정, DB 무결성 등을 검사하고 구조화된 진단 결과(JSON)를 반환합니다.
ANSI 코드나 print 문을 사용하지 않고 순수 데이터 형태로 결과를 제공합니다.
"""
import json
import os
import socket
import shutil
import sys
import importlib
import time
from pathlib import Path
from typing import Mapping, Optional, Tuple, TypeAlias
from sari.core.cjk import lindera_available, lindera_dict_uri, lindera_error
from sari.core.db import LocalSearchDB
from sari.core.config import Config
from sari.core.settings import settings
from sari.core.workspace import WorkspaceManager
from sari.core.server_registry import ServerRegistry, get_registry_path
from sari.mcp.cli.mcp_client import identify_sari_daemon, probe_sari_daemon, is_http_running as _is_http_running
from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address

DoctorResult: TypeAlias = dict[str, object]
DoctorResults: TypeAlias = list[DoctorResult]
ActionItem: TypeAlias = dict[str, str]
ActionItems: TypeAlias = list[ActionItem]


def _read_pid(host: str, port: int) -> Optional[int]:
    try:
        # Prefer CLI helper first for compatibility with tests/legacy behavior.
        from sari.mcp.cli import read_pid as cli_read_pid
        pid = cli_read_pid(host, port)
        if pid:
            return int(pid)
    except Exception:
        pass
    try:
        reg = ServerRegistry()
        inst = reg.resolve_daemon_by_endpoint(host, port)
        return int(inst["pid"]) if inst and inst.get("pid") else None
    except Exception:
        return None


def _get_http_host_port(
        port_override: Optional[int] = None) -> Tuple[str, int]:
    # Simplified logic for doctor to avoid cli dependency
    from sari.core.constants import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT
    host = os.environ.get("SARI_HTTP_HOST") or DEFAULT_HTTP_HOST
    port = port_override or int(os.environ.get(
        "SARI_HTTP_PORT") or DEFAULT_HTTP_PORT)
    return host, port


def _identify_sari_daemon(host: str, port: int):
    return identify_sari_daemon(host, port)


_cli_identify = _identify_sari_daemon


def _result(name: str, passed: bool, error: str = "",
            warn: bool = False) -> DoctorResult:
    """진단 결과를 딕셔너리 형태로 반환합니다."""
    return {"name": name, "passed": passed, "error": error, "warn": warn}


def _row_get(row: object, key: str, index: int, default: object = None) -> object:
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


def _check_db(ws_root: str) -> DoctorResults:
    """데이터베이스 설정, 접근 권한, 스키마 등을 검사합니다."""
    results: DoctorResults = []
    cfg_path = WorkspaceManager.resolve_config_path(ws_root)
    cfg = Config.load(cfg_path, workspace_root_override=ws_root)
    # 자동 수정: 설정 파일에 db_path가 없으면 현재 설정값으로 저장
    try:
        if cfg_path and Path(cfg_path).exists():
            raw = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict) and not raw.get(
                    "db_path") and cfg.db_path:
                raw["db_path"] = cfg.db_path
                Path(cfg_path).parent.mkdir(parents=True, exist_ok=True)
                Path(cfg_path).write_text(
                    json.dumps(
                        raw,
                        ensure_ascii=False,
                        indent=2) + "\n",
                    encoding="utf-8")
                results.append(
                    _result(
                        "DB Path AutoFix",
                        True,
                        f"db_path set to {cfg.db_path}"))
    except Exception as e:
        results.append(_result("DB Path AutoFix", False, f"failed: {e}"))
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        results.append(
            _result(
                "DB Existence",
                False,
                f"DB not found at {db_path}"))
        return results

    try:
        db = LocalSearchDB(str(db_path))
    except Exception as e:
        results.append(_result("DB Access", False, str(e)))
        return results

    # FTS5 모듈 지원 여부 확인
    fts_ok = False
    try:
        cursor = db.db.connection().execute("PRAGMA compile_options")
        options = [str(_row_get(r, "compile_options", 0, "") or "") for r in cursor.fetchall()]
        fts_ok = "ENABLE_FTS5" in options
    except Exception:
        fts_ok = False

    results.append(
        _result(
            "DB FTS5 Support",
            fts_ok,
            "FTS5 module missing in SQLite" if not fts_ok else ""))
    try:
        def _cols(table: str) -> list[str]:
            row = db.db.connection().execute(f"PRAGMA table_info({table})")
            return [str(_row_get(r, "name", 1, "") or "") for r in row.fetchall()]

        # 주요 테이블 컬럼 존재 여부 확인 (스키마 검증)
        symbols_cols = _cols("symbols")
        if "end_line" in symbols_cols:
            results.append(_result("DB Schema", True))
        else:
            results.append(
                _result(
                    "DB Schema",
                    False,
                    "Column 'end_line' missing in 'symbols'. Run update."))
        if "qualname" in symbols_cols and "symbol_id" in symbols_cols:
            results.append(_result("DB Schema Symbol IDs", True))
        else:
            results.append(
                _result(
                    "DB Schema Symbol IDs",
                    False,
                    "Missing qualname/symbol_id in 'symbols'."))
        rel_cols = _cols("symbol_relations")
        if "from_symbol_id" in rel_cols and "to_symbol_id" in rel_cols:
            results.append(_result("DB Schema Relations IDs", True))
        else:
            results.append(
                _result(
                    "DB Schema Relations IDs",
                    False,
                    "Missing from_symbol_id/to_symbol_id in 'symbol_relations'."))
        snippet_cols = _cols("snippets")
        if "anchor_before" in snippet_cols and "anchor_after" in snippet_cols:
            results.append(_result("DB Schema Snippet Anchors", True))
        else:
            results.append(
                _result(
                    "DB Schema Snippet Anchors",
                    False,
                    "Missing anchor_before/anchor_after in 'snippets'."))
        ctx_cols = _cols("contexts")
        if all(
            c in ctx_cols for c in (
                "source",
                "valid_from",
                "valid_until",
                "deprecated")):
            results.append(_result("DB Schema Context Validity", True))
        else:
            results.append(
                _result(
                    "DB Schema Context Validity",
                    False,
                    "Missing validity columns in 'contexts'."))
        row = db.db.connection().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='snippet_versions'"
        ).fetchone()
        results.append(
            _result(
                "DB Schema Snippet Versions",
                bool(row),
                "snippet_versions table missing" if not row else ""))
        row = db.db.connection().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='failed_tasks'"
        ).fetchone()
        results.append(
            _result(
                "DB Schema Failed Tasks",
                bool(row),
                "failed_tasks table missing" if not row else ""))

        # 실패한 작업(DLQ) 상태 확인
        if row:
            total_failed, high_failed = db.count_failed_tasks()
            if high_failed >= 3:
                results.append(
                    _result(
                        "Failed Tasks (DLQ)",
                        False,
                        f"{high_failed} tasks exceeded retry threshold"))
            else:
                results.append(
                    _result(
                        "Failed Tasks (DLQ)",
                        True,
                        f"pending={total_failed}"))
    except Exception as e:
        results.append(_result("DB Schema Check", False, str(e)))
    finally:
        try:
            db.close()
        except Exception:
            pass

    return results


def _platform_tokenizer_tag() -> str:
    """현재 플랫폼에 맞는 토크나이저 태그를 반환합니다."""
    import platform
    plat = sys.platform
    arch = platform.machine().lower()
    if plat.startswith("darwin"):
        if arch in {"arm64", "aarch64"}:
            return "macosx_11_0_arm64"
        if arch in {"x86_64", "amd64"}:
            return "macosx_10_9_x86_64"
        return "macosx"
    if plat.startswith("win"):
        return "win_amd64"
    if plat.startswith("linux"):
        if arch in {"aarch64", "arm64"}:
            return "manylinux_2_17_aarch64"
        return "manylinux_2_17_x86_64"
    return "unknown"


def _check_engine_tokenizer_data() -> DoctorResult:
    """토크나이저 데이터(lindera 등)가 설치되어 있는지 확인합니다."""
    if lindera_available():
        uri = lindera_dict_uri() or "embedded://ipadic"
        return _result("CJK Tokenizer", True, f"active ({uri})")
    err = lindera_error() or "not available"
    return _result(
        "CJK Tokenizer",
        False,
        f"{err} (package 'lindera-python-ipadic' optional)")


def _check_tree_sitter() -> DoctorResult:
    """Tree-sitter 패키지와 언어 파서가 설치되어 있는지 확인합니다."""
    try:
        from sari.core.parsers.ast_engine import ASTEngine
        engine = ASTEngine()
        if not engine.enabled:
            return _result(
                "Tree-sitter Support",
                False,
                "core 'tree-sitter' package not installed (optional)")

        # 주요 언어 파서 확인
        langs = [
            "python",
            "javascript",
            "typescript",
            "java",
            "go",
            "rust",
            "cpp"]
        installed = []
        for lang in langs:
            if engine._get_language(lang):
                installed.append(lang)

        if installed:
            return _result(
                "Tree-sitter Support",
                True,
                f"enabled for: {', '.join(installed)}")
        return _result(
            "Tree-sitter Support",
            True,
            "core enabled but no language parsers loaded yet")
    except Exception as e:
        return _result("Tree-sitter Support", False, str(e))


def _check_embedded_engine_module() -> DoctorResult:
    """내장 엔진 모듈(tantivy)이 임포트 가능한지 확인합니다."""
    try:
        import tantivy  # type: ignore
        ver = getattr(tantivy, "__version__", "unknown")
        return _result("Embedded Engine Module", True, str(ver))
    except Exception as e:
        return _result(
            "Embedded Engine Module",
            False,
            f"{type(e).__name__}: {e}")


def _check_windows_write_lock_support() -> DoctorResult:
    """Windows 환경에서 파일 쓰기 잠금(locking)이 지원되는지 확인합니다."""
    if os.name != "nt":
        return _result("Windows Write Lock", True, "non-windows platform")
    try:
        import msvcrt  # type: ignore
        ok = hasattr(msvcrt, "locking") and hasattr(msvcrt, "LK_LOCK")
        if ok:
            return _result(
                "Windows Write Lock",
                True,
                "msvcrt.locking available")
        return _result(
            "Windows Write Lock",
            False,
            "msvcrt.locking is unavailable")
    except Exception as e:
        return _result("Windows Write Lock", False, f"{type(e).__name__}: {e}")


def _check_db_migration_safety() -> DoctorResult:
    """DB 마이그레이션 도구(peewee 등)의 안전성을 확인합니다."""
    # Legacy check removed as we moved to peewee + init_schema
    return _result(
        "DB Migration Safety",
        True,
        "using peewee init_schema (idempotent)")


def _check_engine_sync_dlq(ws_root: str) -> DoctorResult:
    """엔진 동기화 실패 기록(DLQ)이 있는지 확인합니다."""
    try:
        cfg_path = WorkspaceManager.resolve_config_path(ws_root)
        cfg = Config.load(cfg_path, workspace_root_override=ws_root)
        db = LocalSearchDB(cfg.db_path)
        try:
            row = db.db.connection().execute(
                "SELECT COUNT(*), COALESCE(MAX(attempts),0) FROM failed_tasks WHERE error LIKE 'engine_sync_error:%'"
            ).fetchone()
            count = int(_row_get(row, "COUNT(*)", 0, 0) or 0)
            max_attempts = int(_row_get(row, "COALESCE(MAX(attempts),0)", 1, 0) or 0)
            if count == 0:
                return _result(
                    "Engine Sync DLQ",
                    True,
                    "no pending engine_sync_error tasks")
            return _result(
                "Engine Sync DLQ",
                False,
                f"pending={count} max_attempts={max_attempts}")
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception as e:
        return _result("Engine Sync DLQ", False, str(e))


def _check_writer_health(db: object = None) -> DoctorResult:
    """DB Writer 스레드의 상태를 확인합니다."""
    try:
        from sari.core.db.storage import GlobalStorageManager
        sm = getattr(GlobalStorageManager, "_instance", None)
        if sm is None:
            return _result("Writer Health", True, "no active storage manager")
        writer = getattr(sm, "writer", None)
        if writer is None:
            return _result(
                "Writer Health",
                False,
                "storage manager has no writer")
        thread_obj = getattr(writer, "_thread", None)
        alive = bool(thread_obj and thread_obj.is_alive())
        qsize = int(writer.qsize()) if hasattr(writer, "qsize") else -1
        last_commit = int(getattr(writer, "last_commit_ts", 0) or 0)
        age = int(time.time()) - last_commit if last_commit > 0 else -1
        if not alive:
            return _result(
                "Writer Health",
                False,
                f"writer thread not alive (queue={qsize})")
        if qsize > 1000:
            return _result(
                "Writer Health",
                True,
                f"writer alive but queue high={qsize}",
                warn=True)
        if qsize > 0 and age > 120:
            return _result(
                "Writer Health",
                True,
                f"writer alive with stale commits age_sec={age}",
                warn=True)
        detail = f"alive=true queue={qsize}"
        if age >= 0:
            detail += f" last_commit_age_sec={age}"
        return _result("Writer Health", True, detail)
    except Exception as e:
        return _result("Writer Health", False, str(e))


def _check_storage_switch_guard() -> DoctorResult:
    """스토리지 전환이 차단되어 있는지 확인합니다."""
    try:
        from sari.core.db.storage import GlobalStorageManager
        reason = str(
            getattr(
                GlobalStorageManager,
                "_last_switch_block_reason",
                "") or "")
        ts = float(
            getattr(
                GlobalStorageManager,
                "_last_switch_block_ts",
                0.0) or 0.0)
        if not reason:
            return _result("Storage Switch Guard", True, "no blocked switch")
        age = int(time.time() - ts) if ts > 0 else -1
        msg = f"switch blocked: {reason}"
        if age >= 0:
            msg += f" age_sec={age}"
        return _result("Storage Switch Guard", False, msg)
    except Exception as e:
        return _result("Storage Switch Guard", False, str(e))


def _check_fts_rebuild_policy() -> DoctorResult:
    """FTS 재구축 정책 설정을 확인합니다."""
    if settings.FTS_REBUILD_ON_START:
        return _result(
            "FTS Rebuild Policy",
            True,
            "FTS_REBUILD_ON_START=true may increase startup latency",
            warn=True)
    return _result("FTS Rebuild Policy", True, "FTS_REBUILD_ON_START=false")


def _check_engine_runtime(ws_root: str) -> DoctorResult:
    """현재 실행 중인 검색 엔진의 상태를 확인합니다."""
    try:
        cfg_path = WorkspaceManager.resolve_config_path(ws_root)
        cfg = Config.load(cfg_path, workspace_root_override=ws_root)
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
                return _result("Search Engine Runtime", passed, detail)
            return _result(
                "Search Engine Runtime",
                False,
                "engine has no status()")
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
        return _result("Search Engine Runtime", False, str(e))


def _check_lindera_dictionary() -> DoctorResult:
    # Merged into CJK Tokenizer check above
    return _check_engine_tokenizer_data()


def _check_port(port: int, label: str) -> DoctorResult:
    """특정 포트의 가용성을 확인합니다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return _result(f"{label} Port {port} Availability", True)
    except OSError as e:
        return _result(
            f"{label} Port {port} Availability",
            False,
            f"Address in use or missing permission: {e}")
    finally:
        try:
            s.close()
        except Exception:
            pass


def _check_network() -> DoctorResult:
    """외부 네트워크(Google DNS) 연결을 확인합니다."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return _result("Network Check", True)
    except OSError as e:
        return _result("Network Check", False, f"Unreachable: {e}")


def _check_disk_space(ws_root: str, min_gb: float) -> DoctorResult:
    """워크스페이스 경로의 디스크 여유 공간을 확인합니다."""
    try:
        total, used, free = shutil.disk_usage(ws_root)
        free_gb = free / (1024**3)
        if free_gb < min_gb:
            return _result(
                "Disk Space",
                False,
                f"Low space: {free_gb:.2f} GB (Min: {min_gb} GB)")
        return _result("Disk Space", True)
    except Exception as e:
        return _result("Disk Space", False, str(e))


def _check_db_integrity(ws_root: str) -> DoctorResult:
    """SQLite DB 파일에 대해 깊은 무결성 검사를 수행합니다."""
    try:
        cfg_path = WorkspaceManager.resolve_config_path(ws_root)
        cfg = Config.load(cfg_path, workspace_root_override=ws_root)
        db_path = Path(cfg.db_path)

        if not db_path.exists():
            return _result("DB Integrity", False, "DB file missing")
        if db_path.stat().st_size == 0:
            return _result("DB Integrity", False, "DB file is 0 bytes (empty)")

        import sqlite3
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            cursor = conn.execute("PRAGMA integrity_check(10)")
            row = cursor.fetchone()
            res = str(next(iter(row), "")) if row else ""
            if res == "ok":
                return _result("DB Integrity", True, "SQLite format ok")
            return _result(
                "DB Integrity",
                False,
                f"Corruption detected: {res}")
    except Exception as e:
        return _result("DB Integrity", False, f"Check failed: {e}")


def _check_log_errors() -> DoctorResult:
    """
    최근 로그 파일에서 ERROR 또는 CRITICAL 패턴을 스캔합니다.
    OOM 방지를 위해 마지막 1MB만 읽고 최근 500줄만 검사합니다.
    """
    try:
        env_log_dir = os.environ.get("SARI_LOG_DIR")
        log_dir = Path(env_log_dir).expanduser().resolve(
        ) if env_log_dir else WorkspaceManager.get_global_log_dir()
        log_file = log_dir / "daemon.log"
        if not log_file.exists():
            return _result("Log Health", True, "No log file yet")

        errors = []
        # 안전 장치: 파일의 마지막 1MB만 읽음
        file_size = log_file.stat().st_size
        read_size = min(file_size, 1024 * 1024)  # 1MB

        with open(log_file, "rb") as f:
            if file_size > read_size:
                f.seek(file_size - read_size)
            chunk = f.read().decode("utf-8", errors="ignore")
            lines = chunk.splitlines()
            # 마지막 500줄에서만 에러 검색
            for line in lines[-500:]:
                line_upper = line.upper()
                if "ERROR" in line_upper or "CRITICAL" in line_upper:
                    errors.append(line.strip())

        if not errors:
            return _result("Log Health", True, "No recent errors")

        # 중복 에러 메시지 제거 (증상 요약)
        unique_errs = []
        for e in errors:
            msg = e.split(" - ")[-1] if " - " in e else e
            if msg not in unique_errs:
                unique_errs.append(msg)

        return _result(
            "Log Health",
            False,
            f"Found {len(errors)} error(s). Symptoms: {', '.join(unique_errs[:3])}")
    except (PermissionError, OSError) as e:
        return _result("Log Health", False, f"Log file inaccessible: {e}")
    except Exception as e:
        return _result("Log Health", True, f"Scan skipped: {e}", warn=True)


def _check_system_env() -> DoctorResults:
    """시스템 환경 정보(플랫폼, Python 버전, 주요 환경변수)를 확인합니다."""
    import platform
    results = []
    results.append(
        _result(
            "Platform",
            True,
            f"{platform.system()} {platform.release()} ({platform.machine()})"))
    results.append(_result("Python", True, sys.version.split()[0]))

    # 중요 환경변수 확인
    roots = os.environ.get("SARI_WORKSPACE_ROOT")
    results.append(
        _result(
            "Env: SARI_WORKSPACE_ROOT",
            bool(roots),
            roots or "Not set"))

    try:
        reg_path = str(get_registry_path())
        results.append(_result("Registry Path", True, reg_path))
        # 쓰기 권한 확인
        if os.path.exists(reg_path):
            if not os.access(reg_path, os.W_OK):
                results.append(
                    _result(
                        "Registry Access",
                        False,
                        "Registry file is read-only"))
        elif not os.access(os.path.dirname(reg_path), os.W_OK):
            results.append(
                _result(
                    "Registry Access",
                    False,
                    "Registry directory is not writable"))
    except Exception as e:
        results.append(
            _result(
                "Registry Path",
                False,
                f"Could not determine registry: {e}"))

    return results


def _check_process_resources(pid: int) -> DoctorResult:
    """특정 프로세스의 리소스 사용량(메모리, CPU)을 확인합니다."""
    try:
        import psutil
        proc = psutil.Process(pid)
        with proc.oneshot():
            mem = proc.memory_info().rss / (1024 * 1024)
            cpu = proc.cpu_percent(interval=0.1)
            return {"mem_mb": round(mem, 1), "cpu_pct": cpu}
    except Exception:
        return {}


def _check_daemon() -> DoctorResult:
    """Sari 데몬 프로세스의 실행 여부와 상태를 점검합니다."""
    host, port = get_daemon_address()
    identify = _identify_sari_daemon(host, port)
    running = identify is not None

    local_version = settings.VERSION
    details = {}

    if running:
        pid = _read_pid(host, port)
        remote_version = identify.get("version", "unknown")
        draining = identify.get("draining", False)

        if pid:
            details = _check_process_resources(pid)

        status_msg = f"Running on {host}:{port} (PID: {pid}, v{remote_version})"
        if draining:
            status_msg += " [DRAINING]"
        if details:
            status_msg += f" [Mem: {details.get('mem_mb')}MB, CPU: {details.get('cpu_pct')}%]"

        if remote_version != local_version:
            return _result(
                "Sari Daemon",
                False,
                f"Version mismatch: local=v{local_version}, remote=v{remote_version}. {status_msg}")

        return _result("Sari Daemon", True, status_msg)

    try:
        reg = ServerRegistry()
        data = reg._load()
        for info in (data.get("daemons") or {}).values():
            if str(info.get("host") or "") != str(host):
                continue
            if int(info.get("port") or 0) != int(port):
                continue
            pid = int(info.get("pid") or 0)
            if pid <= 0:
                continue
            try:
                os.kill(pid, 0)
                return _result(
                    "Sari Daemon",
                    False,
                    f"Not responding on {host}:{port} but PID {pid} is alive. Possible zombie or port conflict.")
            except Exception:
                return _result(
                    "Sari Daemon",
                    False,
                    f"Not running, but stale registry entry exists (PID: {pid}).")
    except Exception:
        pass

    return _result("Sari Daemon", False, "Not running")


def _check_http_service(host: str, port: int) -> DoctorResult:
    """HTTP API 서버의 실행 여부를 확인합니다."""
    running = _is_http_running(host, port)
    if running:
        return _result("HTTP API", True, f"Running on {host}:{port}")
    return _result("HTTP API", False, f"Not running on {host}:{port}")


def _check_search_first_usage(
        usage: Mapping[str, object], mode: str) -> DoctorResult:
    """검색 우선(Search-First) 정책 준수 여부를 확인합니다."""
    violations = int(usage.get("read_without_search", 0))
    searches = int(usage.get("search", 0))
    symbol_searches = int(usage.get("search_symbols", 0))
    if violations == 0:
        return _result("Search-First Usage", True, "")
    policy = mode if mode in {"off", "warn", "enforce"} else "unknown"
    error = (
        f"Search-first policy {policy}: {violations} read call(s) without prior search "
        f"(search={searches}, search_symbols={symbol_searches}).")
    return _result("Search-First Usage", False, error)


def _check_callgraph_plugin() -> DoctorResult:
    """콜 그래프 플러그인 로드 상태를 확인합니다."""
    mod_path = os.environ.get("SARI_CALLGRAPH_PLUGIN", "").strip()
    if not mod_path:
        return _result("CallGraph Plugin", True, "not configured")
    mods = [m.strip() for m in mod_path.split(",") if m.strip()]
    failed = []
    meta = []
    for m in mods:
        try:
            mod = importlib.import_module(m)
            version = getattr(mod, "__version__", "")
            api = getattr(mod, "__callgraph_plugin_api__", None)
            if api is not None:
                meta.append(f"{m}@{version} api={api}")
            else:
                meta.append(f"{m}{'@' + str(version) if version else ''}")
        except Exception:
            failed.append(m)
    if failed:
        return _result(
            "CallGraph Plugin",
            False,
            f"failed to load: {', '.join(failed)}")
    detail = "loaded"
    if meta:
        detail = "loaded: " + ", ".join(meta)
    return _result("CallGraph Plugin", True, detail)


def _recommendations(results: DoctorResults) -> ActionItems:
    """진단 결과에 따른 한국어 권장 조치 사항을 생성합니다."""
    recs: ActionItems = []
    for r in results:
        if r.get("passed"):
            continue
        name = str(r.get("name") or "")
        if name == "DB Existence":
            recs.append(
                {"name": name, "action": "Run `sari init` to create config, then start daemon or run a full scan."})
        elif name == "DB Access":
            recs.append(
                {"name": name, "action": "Check file permissions and ensure no other process is locking the DB."})
        elif name in {
            "DB Schema Symbol IDs",
            "DB Schema Relations IDs",
            "DB Schema Snippet Anchors",
            "DB Schema Context Validity",
            "DB Schema Snippet Versions",
        }:
            recs.append(
                {"name": name, "action": "Upgrade to latest code and run a full rescan."})
        elif name == "Engine Tokenizer Data":
            recs.append(
                {"name": name, "action": "Install CJK support: pip install 'sari[cjk]'"})
        elif name == "Lindera Dictionary":
            recs.append(
                {"name": name, "action": "Install CJK support: pip install 'sari[cjk]'"})
        elif name == "CJK Tokenizer Data" or name == "Lindera Engine":
            recs.append(
                {"name": name, "action": "Install CJK support: pip install 'sari[cjk]'"})
        elif name == "Tree-sitter Support":
            recs.append(
                {"name": name, "action": "Install high-precision parsers: pip install 'sari[treesitter]'"})
        elif name.startswith("Daemon Port") or name.startswith("HTTP Port"):
            recs.append(
                {"name": name, "action": "Change port or stop the conflicting process."})
        elif name == "Sari Daemon":
            recs.append(
                {"name": name, "action": "Start the daemon using `sari daemon start`."})
        elif name == "Network Check":
            recs.append(
                {"name": name, "action": "Ensure internet access or use include_network=false if offline."})
        elif name == "Disk Space":
            recs.append(
                {"name": name, "action": "Free up space or move the workspace to a larger volume."})
        elif name == "Search-First Usage":
            recs.append(
                {"name": name, "action": "Enable search-first enforcement or ensure client searches before reading."})
        elif name == "Workspace Overlap":
            recs.append(
                {"name": name, "action": "Remove nested workspaces from MCP settings. Keep only the top-level root or individual project roots."})
        elif name == "Windows Write Lock":
            recs.append(
                {"name": name, "action": "msvcrt.locking is required on Windows. Use a supported Python runtime."})
        elif name == "DB Migration Safety":
            recs.append(
                {"name": name, "action": "Disable destructive migration paths and keep additive schema initialization strategy."})
        elif name == "Engine Sync DLQ":
            recs.append(
                {"name": name, "action": "Run rescan/retry and check engine status until pending sync-error tasks are cleared."})
        elif name == "Writer Health":
            recs.append(
                {"name": name, "action": "Restart daemon if writer thread is dead and check DB/engine logs for the root error."})
        elif name == "Storage Switch Guard":
            recs.append(
                {"name": name, "action": "Restart process to clear blocked storage switch state and check shutdown behavior."})
    return recs


def _auto_fixable(results: DoctorResults) -> ActionItems:
    """자동 수정 가능한 항목들을 식별하여 액션 목록을 반환합니다."""
    actions: ActionItems = []
    for r in results:
        if r.get("passed"):
            continue
        name = str(r.get("name") or "")
        error = str(r.get("error") or "")

        if name == "DB Schema Symbol IDs":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "DB Schema Relations IDs":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "DB Schema Snippet Anchors":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "DB Schema Context Validity":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "DB Schema Snippet Versions":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "Sari Daemon" and "stale registry entry" in error:
            actions.append(
                {"name": name, "action": "cleanup_registry_daemons"})
        elif name == "Sari Daemon" and "Version mismatch" in error:
            actions.append({"name": name, "action": "restart_daemon"})

    # 레지스트리 손상 확인 (SSOT Check)
    try:
        from sari.core.server_registry import ServerRegistry
        reg = ServerRegistry()
        data = reg._load()
        if not data or data.get("version") != ServerRegistry.VERSION:
            actions.append({"name": "Server Registry",
                           "action": "repair_registry"})
    except Exception:
        actions.append({"name": "Server Registry",
                       "action": "repair_registry"})

    return actions


def _run_auto_fixes(
        ws_root: str, actions: ActionItems) -> DoctorResults:
    """식별된 자동 수정 액션들을 실행합니다."""
    if not actions:
        return []
    results: DoctorResults = []

    for action in actions:
        act = action["action"]
        name = action["name"]

        try:
            if act == "db_migrate":
                cfg_path = WorkspaceManager.resolve_config_path(ws_root)
                cfg = Config.load(cfg_path, workspace_root_override=ws_root)
                db = LocalSearchDB(cfg.db_path)
                db.close()
                results.append(
                    _result(
                        f"Auto Fix {name}",
                        True,
                        "Schema migration applied"))

            elif act == "cleanup_registry_daemons":
                from sari.core.server_registry import ServerRegistry
                reg = ServerRegistry()
                reg.prune_dead()
                results.append(
                    _result(
                        f"Auto Fix {name}",
                        True,
                        "Stale daemon registry entries pruned"))

            elif act == "repair_registry":
                from sari.core.server_registry import ServerRegistry
                reg = ServerRegistry()
                reg._save(reg._empty())  # Reset to clean state
                results.append(
                    _result(
                        f"Auto Fix {name}",
                        True,
                        "Corrupted registry file reset"))

            elif act == "restart_daemon":
                # 이전 데몬 중지 및 재시작 제안
                from sari.mcp.cli.legacy_cli import cmd_daemon_stop

                class Args:
                    daemon_host = ""
                    daemon_port = None
                cmd_daemon_stop(Args())
                results.append(
                    _result(
                        f"Auto Fix {name}",
                        True,
                        "Incompatible daemon stopped. It will restart on next CLI use."))

        except Exception as e:
            results.append(_result(f"Auto Fix {name}", False, str(e)))

    return results


def _run_rescan(ws_root: str) -> DoctorResults:
    """자동 수정 후 재스캔(Rescan)을 실행합니다."""
    results: DoctorResults = []
    results.append(
        _result(
            "Auto Fix Rescan Start",
            True,
            "scan_once starting"))
    try:
        cfg_path = WorkspaceManager.resolve_config_path(ws_root)
        cfg = Config.load(cfg_path, workspace_root_override=ws_root)
        db = LocalSearchDB(cfg.db_path)
        from sari.core.indexer import Indexer
        indexer = Indexer(
            cfg,
            db,
            indexer_mode="leader",
            indexing_enabled=True,
            startup_index_enabled=True)
        indexer.scan_once()
        db.close()
        results.append(_result("Auto Fix Rescan", True, "scan_once completed"))
    except Exception as e:
        results.append(_result("Auto Fix Rescan", False, str(e)))
    return results


def _check_workspace_overlaps(ws_root: str) -> DoctorResults:
    """등록된 여러 워크스페이스 간의 중첩(Overlap)을 감지하여 중복 인덱싱을 방지합니다."""
    results = []
    try:
        from sari.core.server_registry import ServerRegistry
        reg = ServerRegistry()
        data = reg._load()
        workspaces = list(data.get("workspaces", {}).keys())

        current = WorkspaceManager.normalize_path(ws_root)
        overlaps = []
        for ws in workspaces:
            norm_ws = WorkspaceManager.normalize_path(ws)
            if norm_ws == current:
                continue

            # 부모-자식 관계 확인
            if current.startswith(
                norm_ws +
                os.sep) or norm_ws.startswith(
                current +
                    os.sep):
                overlaps.append(norm_ws)

        if overlaps:
            results.append(
                _result(
                    "Workspace Overlap",
                    False,
                    f"Nesting detected with: {', '.join(overlaps)}. This leads to duplicate indexing."))
        else:
            results.append(
                _result(
                    "Workspace Overlap",
                    True,
                    "No nested roots detected"))
    except Exception as e:
        results.append(
            _result(
                "Workspace Overlap Check",
                True,
                f"Skipped: {e}",
                warn=True))
    return results


def execute_doctor(
    args: object,
    db: object = None,
    logger: object = None,
    roots: Optional[list[str]] = None,
) -> dict[str, object]:
    """Doctor 도구 실행 핸들러."""
    if not isinstance(args, Mapping):
        try:
            from sari.mcp.tools._util import mcp_response, pack_error, ErrorCode
        except Exception:
            from _util import mcp_response, pack_error, ErrorCode
        msg = "'args' must be an object"
        return mcp_response(
            "doctor",
            lambda: pack_error("doctor", ErrorCode.INVALID_ARGS, msg),
            lambda: {
                "error": {"code": ErrorCode.INVALID_ARGS.value, "message": msg},
                "isError": True,
            },
        )

    args_map: Mapping[str, object] = args
    ws_root = roots[0] if roots else WorkspaceManager.resolve_workspace_root()

    include_network = bool(args_map.get("include_network", True))
    include_port = bool(args_map.get("include_port", True))
    include_db = bool(args_map.get("include_db", True))
    include_disk = bool(args_map.get("include_disk", True))
    include_daemon = bool(args_map.get("include_daemon", True))
    include_venv = bool(args_map.get("include_venv", True))
    bool(args_map.get("include_marker", False))
    port = int(args_map.get("port", 0))
    min_disk_gb = float(args_map.get("min_disk_gb", 1.0))

    results: DoctorResults = []

    results.extend(_check_system_env())

    if include_venv:
        in_venv = sys.prefix != sys.base_prefix
        results.append(
            _result(
                "Virtualenv",
                True,
                "" if in_venv else "Not running in venv (ok)"))

    if include_daemon:
        results.append(_check_daemon())
        results.append(_check_log_errors())

    if include_port:
        daemon_host, daemon_port = get_daemon_address()
        daemon_running = probe_sari_daemon(daemon_host, daemon_port)
        if daemon_running:
            results.append(
                _result(
                    "Daemon Port",
                    True,
                    f"In use by running daemon {daemon_host}:{daemon_port}"))
        else:
            results.append(_check_port(daemon_port, "Daemon"))

        http_host, http_port = _get_http_host_port(
            port_override=port if port else None)
        results.append(_check_http_service(http_host, http_port))
        if not _is_http_running(http_host, http_port):
            results.append(_check_port(http_port, "HTTP"))

    if include_network:
        results.append(_check_network())

    if include_db:
        results.append(_check_db_integrity(ws_root))
        results.extend(_check_db(ws_root))
        results.append(_check_db_migration_safety())
        results.append(_check_windows_write_lock_support())
        results.append(_check_engine_sync_dlq(ws_root))
        results.append(_check_embedded_engine_module())
        results.append(_check_engine_runtime(ws_root))
        results.append(_check_engine_tokenizer_data())
        results.append(_check_tree_sitter())
        results.append(_check_fts_rebuild_policy())
        results.append(_check_writer_health(db))
        results.append(_check_storage_switch_guard())

    if include_disk:
        results.append(_check_disk_space(ws_root, min_disk_gb))

    results.extend(_check_workspace_overlaps(ws_root))

    usage = args_map.get("search_usage")
    if isinstance(usage, dict):
        mode = str(args_map.get("search_first_mode", "unknown"))
        results.append(_check_search_first_usage(usage, mode))

    results.append(_check_callgraph_plugin())

    auto_fix = bool(args_map.get("auto_fix", False))
    auto_fix_rescan = bool(args_map.get("auto_fix_rescan", False))
    auto_fix_results: DoctorResults = []
    if auto_fix:
        actions = _auto_fixable(results)
        auto_fix_results = _run_auto_fixes(ws_root, actions)
        results.extend(auto_fix_results)
        if auto_fix_rescan:
            if any(not r.get("passed") for r in auto_fix_results):
                res = [
                    _result(
                        "Auto Fix Rescan Skipped",
                        False,
                        "auto-fix failed; rescan skipped")]
                auto_fix_results.extend(res)
                results.extend(res)
            else:
                res = _run_rescan(ws_root)
                auto_fix_results.extend(res)
                results.extend(res)

    output = {
        "workspace_root": ws_root,
        "results": results,
        "recommendations": _recommendations(results),
        "auto_fix": auto_fix_results,
    }

    try:
        from sari.mcp.tools._util import mcp_response, pack_header, pack_line
    except Exception:
        from _util import mcp_response, pack_header, pack_line

    def build_pack() -> str:
        compact = str(os.environ.get("SARI_RESPONSE_COMPACT")
                      or "1").strip().lower() not in {"0", "false", "no", "off"}
        payload = json.dumps(output, ensure_ascii=False, separators=(
            ",", ":") if compact else None, indent=None if compact else 2)
        lines = [pack_header("doctor", {}, returned=1)]
        lines.append(pack_line("t", single_value=payload))
        return "\n".join(lines)

    return mcp_response(
        "doctor",
        build_pack,
        lambda: output,  # Return raw dict, let mcp_response handle formatting
    )


if __name__ == "__main__":
    result = execute_doctor({})
    # result is the dict returned by mcp_response
    content = result["content"][0]["text"]
    print(content)
