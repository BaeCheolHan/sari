#!/usr/bin/env python3
"""
로컬 검색 MCP 서버를 위한 진단 도구 (Doctor).
시스템 상태, 설정, DB 무결성 등을 검사하고 구조화된 진단 결과(JSON)를 반환합니다.
ANSI 코드나 print 문을 사용하지 않고 순수 데이터 형태로 결과를 제공합니다.
"""
import json
import os
import sys
import importlib
from typing import Mapping, Optional, TypeAlias
from sari.core.cjk import lindera_available, lindera_dict_uri, lindera_error
from sari.core.config import Config
from sari.core.settings import settings
from sari.core.workspace import WorkspaceManager
from sari.mcp.server_registry import ServerRegistry, get_registry_path
from sari.core.policy_engine import load_daemon_policy, load_daemon_runtime_status
from sari.mcp.cli.mcp_client import probe_sari_daemon, is_http_running as _is_http_running
from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address
from sari.core.daemon_runtime_state import RUNTIME_HOST, RUNTIME_PORT
from sari.mcp.tools.doctor_common import (
    result as _result,
    safe_float as _safe_float,
    safe_int as _safe_int,
    safe_pragma_table_name as _safe_pragma_table_name,
)
from sari.mcp.tools.doctor_daemon_endpoint import (
    identify as _identify_sari_daemon,
    read_pid as _read_pid,
    resolve_http_endpoint_for_daemon as _resolve_http_endpoint_for_daemon,
)
from sari.mcp.tools.doctor_actions import (
    auto_fixable as _auto_fixable,
    check_workspace_overlaps as _check_workspace_overlaps,
    recommendations as _recommendations,
    run_auto_fixes as _run_auto_fixes,
    run_rescan as _run_rescan,
)
from sari.mcp.tools.doctor_checks_db import (
    check_db as _check_db,
    check_db_integrity as _check_db_integrity,
    check_db_migration_safety as _check_db_migration_safety,
    check_engine_runtime as _check_engine_runtime,
    check_engine_sync_dlq as _check_engine_sync_dlq,
    check_fts_rebuild_policy as _check_fts_rebuild_policy,
    check_storage_switch_guard as _check_storage_switch_guard,
    check_writer_health as _check_writer_health,
)
from sari.mcp.tools.doctor_checks_system import (
    check_disk_space as _check_disk_space,
    check_log_errors as _check_log_errors,
    check_network as _check_network,
    check_port as _check_port,
    check_process_resources as _check_process_resources,
    check_system_env as _check_system_env,
)

DoctorResult: TypeAlias = dict[str, object]
DoctorResults: TypeAlias = list[DoctorResult]

_cli_identify = _identify_sari_daemon


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
        f"{err} (install/upgrade package 'lindera-python-ipadic')")


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


def _check_lindera_dictionary() -> DoctorResult:
    # Merged into CJK Tokenizer check above
    return _check_engine_tokenizer_data()


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
        data = reg.get_registry_snapshot(include_dead=True)
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


def _check_daemon_policy() -> DoctorResult:
    policy = load_daemon_policy(settings_obj=settings)
    daemon_host, daemon_port = get_daemon_address()
    http_host, http_port = _resolve_http_endpoint_for_daemon(daemon_host, daemon_port)
    override_keys = (
        "SARI_DAEMON_HEARTBEAT_SEC",
        "SARI_DAEMON_IDLE_SEC",
        "SARI_DAEMON_IDLE_WITH_ACTIVE",
        "SARI_DAEMON_DRAIN_GRACE_SEC",
        "SARI_DAEMON_AUTOSTOP",
        "SARI_DAEMON_AUTOSTOP_GRACE_SEC",
        "SARI_DAEMON_SHUTDOWN_INHIBIT_MAX_SEC",
        "SARI_DAEMON_LEASE_TTL_SEC",
        RUNTIME_HOST,
        RUNTIME_PORT,
        "SARI_HTTP_HOST",
        "SARI_HTTP_PORT",
    )
    overrides = [k for k in override_keys if os.environ.get(k) not in (None, "")]
    detail = (
        f"daemon={daemon_host}:{daemon_port} http={http_host}:{http_port} "
        f"heartbeat_sec={policy.heartbeat_sec} idle_sec={policy.idle_sec} "
        f"idle_with_active={str(policy.idle_with_active).lower()} "
        f"autostop_enabled={str(policy.autostop_enabled).lower()} "
        f"autostop_grace_sec={policy.autostop_grace_sec} "
        f"shutdown_inhibit_max_sec={policy.shutdown_inhibit_max_sec} "
        f"lease_ttl_sec={policy.lease_ttl_sec} "
        f"overrides={','.join(overrides) if overrides else 'none'}"
    )
    return _result("Daemon Policy", True, detail)


def _check_http_service(host: str, port: int) -> DoctorResult:
    """HTTP API 서버의 실행 여부를 확인합니다."""
    running = _is_http_running(host, port)
    if running:
        return _result("HTTP API", True, f"Running on {host}:{port}")
    return _result("HTTP API", False, f"Not running on {host}:{port}")


def _check_daemon_runtime_markers() -> DoctorResult:
    """런타임 마커(daemon_runtime_state) 스냅샷을 점검합니다."""
    try:
        status = load_daemon_runtime_status()
        detail = (
            f"shutdown_intent={str(bool(status.shutdown_intent)).lower()} "
            f"suicide_state={status.suicide_state} "
            f"active_leases={int(status.active_leases_count)} "
            f"event_queue_depth={int(status.event_queue_depth)} "
            f"workers_alive={len(list(status.workers_alive or []))}"
        )
        return _result("Daemon Runtime Markers", True, detail)
    except Exception as e:
        return _result("Daemon Runtime Markers", False, str(e))


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
    include_marker = bool(args_map.get("include_marker", False))
    port = _safe_int(args_map.get("port", 0), 0)
    min_disk_gb = _safe_float(args_map.get("min_disk_gb", 1.0), 1.0)

    auto_fix = bool(args_map.get("auto_fix", False))
    auto_fix_rescan = bool(args_map.get("auto_fix_rescan", False))

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
        results.append(_check_daemon_policy())
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

        http_host, http_port = _resolve_http_endpoint_for_daemon(
            daemon_host, daemon_port, port_override=port if port else None)
        results.append(_check_http_service(http_host, http_port))
        if not _is_http_running(http_host, http_port):
            results.append(_check_port(http_port, "HTTP"))

    if include_network:
        results.append(_check_network())

    if include_db:
        results.append(_check_db_integrity(ws_root))
        results.extend(_check_db(ws_root, allow_config_autofix=auto_fix))
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

    if include_marker:
        results.append(_check_daemon_runtime_markers())

    results.extend(_check_workspace_overlaps(ws_root))

    usage = args_map.get("search_usage")
    if isinstance(usage, dict):
        mode = str(args_map.get("search_first_mode", "unknown"))
        results.append(_check_search_first_usage(usage, mode))

    results.append(_check_callgraph_plugin())

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
