import json
import os
import sys
import threading
import queue
import concurrent.futures
import socket
from uuid import uuid4
from pathlib import Path
from typing import Optional, Mapping, TypeAlias
from sari.mcp.adapters.workspace_runtime import (
    WorkspaceRuntime,
    get_workspace_runtime,
)
from sari.core.workspace import WorkspaceManager
from sari.core.settings import settings
from sari.core.config import Config
from sari.mcp.policies import PolicyEngine
from sari.mcp.middleware import PolicyMiddleware, run_middlewares
from sari.mcp.tools.registry import ToolContext, build_default_registry
from sari.mcp.telemetry import TelemetryLogger
from sari.mcp.transport import McpTransport
from sari.core.utils.logging import get_logger
from sari.mcp.trace import trace
from sari.mcp.server_sanitize import (
    sanitize_for_llm_tools as _sanitize_for_llm_tools_impl,
    sanitize_value as _sanitize_value_impl,
)
from sari.mcp.server_daemon_forward import (
    close_all_daemon_connections as _close_all_daemon_connections_impl,
    close_daemon_connection as _close_daemon_connection_impl,
    ensure_daemon_connection as _ensure_daemon_connection_impl,
    forward_error_response as _forward_error_response_impl,
    forward_over_open_socket as _forward_over_open_socket_impl,
)
from sari.mcp.server_worker import (
    drain_pending_requests as _drain_pending_requests_impl,
    emit_queue_overload as _emit_queue_overload_impl,
    enqueue_incoming_request as _enqueue_incoming_request_impl,
    handle_and_respond as _handle_and_respond_impl,
    submit_request_for_execution as _submit_request_for_execution_impl,
    worker_loop as _worker_loop_impl,
)
from sari.mcp.server_logging import (
    log_debug_message as _log_debug_message_impl,
    log_debug_request as _log_debug_request_impl,
    log_debug_response as _log_debug_response_impl,
)
from sari.mcp.server_bootstrap import build_runtime_options, parse_truthy_flag
from sari.mcp.server_initialize import (
    build_initialize_result as _build_initialize_result_impl,
    choose_target_uri as _choose_target_uri_impl,
    iter_client_protocol_versions as _iter_client_protocol_versions_impl,
    negotiate_protocol_version as _negotiate_protocol_version_impl,
)
from sari.mcp.server_tool_runtime import (
    ensure_connection_id as _ensure_connection_id_impl,
    resolve_tool_runtime as _resolve_tool_runtime_impl,
)
from sari.mcp.server_request_dispatch import (
    execute_local_method as _execute_local_method_impl,
)

try:
    import orjson as _orjson
except Exception:
    _orjson = None

JsonMap: TypeAlias = dict[str, object]


class JsonRpcException(Exception):
    def __init__(self, code: int, message: str, data: object = None):
        self.code = code
        self.message = message
        self.data = data


def _json_dumps(obj: object) -> str:
    if _orjson:
        return _orjson.dumps(obj).decode("utf-8")
    return json.dumps(obj)


MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB


class LocalSearchMCPServer:
    """
    Modernized MCP Server for Sari.
    Delegates workspace management to WorkspaceRegistry.
    """
    PROTOCOL_VERSION = "2025-11-25"
    SUPPORTED_VERSIONS = {
        "2024-11-05",
        "2025-03-26",
        "2025-06-18",
        "2025-11-25"}
    SERVER_NAME = "sari"
    SERVER_VERSION = settings.VERSION
    _SENSITIVE_KEYS = (
        "token",
        "secret",
        "password",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "key")

    def __init__(
            self,
            workspace_root: str,
            cfg: object = None,
            db: object = None,
            indexer: object = None,
            workspace_runtime: Optional[WorkspaceRuntime] = None,
            start_worker: bool = True):
        self.workspace_root = workspace_root
        trace(
            "server_init_start",
            workspace_root=self.workspace_root,
            injected_cfg=bool(cfg),
            injected_db=bool(db),
            injected_indexer=bool(indexer),
            start_worker=start_worker,
        )
        # Keep optional injected handles for backward compatibility with older
        # callers.
        self._injected_cfg = cfg
        self._injected_db = db
        self._injected_indexer = indexer
        # Backward-compatible attribute name kept for tests and legacy code.
        self.registry = workspace_runtime or get_workspace_runtime()
        self.policy_engine = PolicyEngine(mode=settings.SEARCH_FIRST_MODE)
        self.logger = TelemetryLogger(WorkspaceManager.get_global_log_dir())
        self.struct_logger = get_logger("sari.mcp.protocol")
        self._tool_registry = build_default_registry()
        self._middlewares = [PolicyMiddleware(self.policy_engine)]
        runtime_opts = build_runtime_options(
            env=os.environ,
            debug_default=settings.DEBUG,
            queue_size=settings.get_int("MCP_QUEUE_SIZE", 1000),
        )
        self._debug_enabled = runtime_opts.debug_enabled
        self._dev_jsonl = runtime_opts.dev_jsonl
        self._force_content_length = runtime_opts.force_content_length
        # Add maxsize to prevent memory bloat under heavy load
        self._req_queue: "queue.Queue[JsonMap]" = queue.Queue(
            maxsize=runtime_opts.queue_size)
        self._stop = threading.Event()
        self._stdout_lock = threading.Lock()
        self.transport = None
        self._session = None
        self._session_acquired = False
        self._daemon_lock = threading.Lock()
        self._daemon_channels_lock = threading.Lock()
        self._daemon_channels: dict[int, object] = {}
        # Duplicate assignment removed. Use _debug_enabled from above.

        # Daemon proxy is handled by the stdio proxy process, not the MCP
        # server.
        self._proxy_to_daemon = False
        self._daemon_sock = None
        self._server_connection_id = str(uuid4())

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=runtime_opts.max_workers)
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        if start_worker:
            self._worker.start()
        trace(
            "server_init_done",
            workspace_root=self.workspace_root,
            proxy_to_daemon=self._proxy_to_daemon)

    def handle_initialize(self, params: Mapping[str, object]) -> JsonMap:
        trace(
            "initialize_enter",
            workspace_root=self.workspace_root,
            params_keys=sorted(list(params.keys())),
            has_root_uri=bool(params.get("rootUri") or params.get("rootPath")),
            protocol_version=params.get("protocolVersion"),
            supported_versions=params.get("supportedProtocolVersions"),
        )
        target_uri = _choose_target_uri_impl(params)

        if target_uri:
            # Update workspace if provided by client
            self.workspace_root = WorkspaceManager.resolve_workspace_root(
                root_uri=target_uri)
            trace(
                "initialize_resolved_workspace",
                workspace_root=self.workspace_root,
                target_uri=target_uri)

        negotiated_version = self._negotiate_protocol_version(params)
        trace("initialize_negotiated_version", version=negotiated_version)

        return _build_initialize_result_impl(
            negotiated_version, self.SERVER_NAME, self.SERVER_VERSION
        )

    def _iter_client_protocol_versions(
            self, params: Mapping[str, object]) -> list[str]:
        return _iter_client_protocol_versions_impl(params)

    def _negotiate_protocol_version(self, params: Mapping[str, object]) -> str:
        strict = parse_truthy_flag(os.environ.get("SARI_STRICT_PROTOCOL"))
        return _negotiate_protocol_version_impl(
            params=params,
            supported_versions=self.SUPPORTED_VERSIONS,
            default_version=self.PROTOCOL_VERSION,
            strict_protocol=strict,
            error_builder=lambda supported: JsonRpcException(
                -32602,
                "Unsupported protocol version",
                data={"supported": supported},
            ),
        )

    def handle_initialized(self, params: Mapping[str, object]) -> None:
        """Called by client after initialize response is received."""
        # Optional: Start background tasks here if needed
        pass

    def handle_tools_call(self, params: Mapping[str, object]) -> JsonMap:
        tool_name = params.get("name")
        raw_args = params.get("arguments", {})
        base_args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        args = _ensure_connection_id_impl(base_args, self._server_connection_id)
        runtime = _resolve_tool_runtime_impl(
            injected_cfg=self._injected_cfg,
            injected_db=self._injected_db,
            injected_indexer=self._injected_indexer,
            session=self._session,
            registry=self.registry,
            workspace_root=self.workspace_root,
            error_builder=lambda msg: JsonRpcException(-32000, msg),
        )
        if runtime.session is not None:
            self._session = runtime.session
        if runtime.session_acquired:
            self._session_acquired = True

        ctx = ToolContext(
            db=runtime.db,
            engine=getattr(runtime.db, "engine", None),
            indexer=runtime.indexer,
            roots=runtime.roots,
            cfg=self._injected_cfg,
            logger=self.logger,
            workspace_root=self.workspace_root,
            server_version=self.SERVER_VERSION,
            policy_engine=self.policy_engine
        )

        return run_middlewares(
            tool_name,
            ctx,
            args,
            self._middlewares,
            lambda: self._tool_registry.execute(tool_name, ctx, args),
        )

    def list_roots(self) -> list[dict[str, str]]:
        """Return configured workspace roots as MCP root objects."""
        cfg = None
        try:
            cfg_path = WorkspaceManager.resolve_config_path(
                self.workspace_root)
            cfg = Config.load(
                cfg_path, workspace_root_override=self.workspace_root)
        except Exception:
            cfg = None
        config_roots = list(
            getattr(
                cfg,
                "workspace_roots",
                []) or []) if cfg else []
        roots = WorkspaceManager.resolve_workspace_roots(
            root_uri=f"file://{self.workspace_root}",
            config_roots=config_roots,
        )
        result = []
        for r in roots:
            name = Path(r).name or r
            result.append({"uri": f"file://{r}", "name": name})
        return result

    @staticmethod
    def _sanitize_for_llm_tools(schema: dict) -> dict:
        return _sanitize_for_llm_tools_impl(schema)

    def list_tools(self) -> list[dict[str, object]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": self._sanitize_for_llm_tools(t.input_schema),
            }
            for t in self._tool_registry.list_tools_raw()
        ]

    def handle_tools_list(self, params: Mapping[str, object]) -> JsonMap:
        """Test helper: Handle tools/list request."""
        return {"tools": self.list_tools()}

    def _ensure_initialized(self) -> None:
        """Test helper: Ensure session is initialized."""
        if self._session is None and self._injected_db is None:
            self._session = self.registry.get_or_create(self.workspace_root)
            self._session_acquired = True

    def _tool_status(self, args: dict[str, object]) -> JsonMap:
        """Test helper: Execute status tool."""
        self._ensure_initialized()
        return self.handle_tools_call({"name": "status", "arguments": args})

    def _tool_search(self, args: dict[str, object]) -> JsonMap:
        """Test helper: Execute search tool."""
        self._ensure_initialized()
        return self.handle_tools_call({"name": "search", "arguments": args})

    def _dispatch_methods(self) -> dict[str, object]:
        return {
            "initialize": self.handle_initialize,
            "sari/identify": lambda _params: {
                "name": self.SERVER_NAME,
                "version": self.SERVER_VERSION,
                "workspaceRoot": self.workspace_root,
                "pid": os.getpid(),
            },
            "tools/list": lambda _params: {"tools": self.list_tools()},
            "prompts/list": lambda _params: {"prompts": []},
            "resources/list": lambda _params: {"resources": []},
            "resources/templates/list": lambda _params: {"resourceTemplates": []},
            "roots/list": lambda _params: {"roots": self.list_roots()},
            "initialized": lambda _params: {},
            "notifications/initialized": lambda _params: {},
            "ping": lambda _params: {},
        }

    def handle_request(
            self, request: object) -> Optional[JsonMap]:
        if not isinstance(request, Mapping):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request"},
            }
        request_map: Mapping[str, object] = request
        trace(
            "handle_request_enter",
            method=request_map.get("method"),
            msg_id=request_map.get("id"),
            proxy_to_daemon=self._proxy_to_daemon,
        )
        if self._proxy_to_daemon:
            resp = self._forward_to_daemon(dict(request_map))
            if isinstance(resp, dict):
                err = resp.get("error") if isinstance(resp, dict) else None
                if isinstance(err, dict) and err.get("code") == -32002:
                    # Daemon is unreachable; fall back to local stdio handling.
                    self._log_debug(
                        "Daemon proxy failed; falling back to local MCP server.")
                    self._proxy_to_daemon = False
                    self._close_all_daemon_connections()
                    trace("daemon_proxy_fallback", error=err)
                else:
                    trace(
                        "handle_request_proxy_response",
                        has_error=bool(err),
                        msg_id=request_map.get("id"))
                    return resp
            else:
                return resp

        method, params, msg_id = request_map.get(
            "method"), request_map.get("params", {}), request_map.get("id")
        if msg_id is None:
            return None  # Ignore notifications for now

        try:
            resp = _execute_local_method_impl(
                method=method,
                params=params if isinstance(params, Mapping) else {},
                msg_id=msg_id,
                handle_tools_call=self.handle_tools_call,
                dispatch_methods=self._dispatch_methods(),
            )
            trace(
                "handle_request_exit",
                method=method,
                msg_id=msg_id,
                ok="error" not in resp,
            )
            return resp
        except JsonRpcException as e:
            trace(
                "handle_request_error",
                method=method,
                msg_id=msg_id,
                code=e.code,
                message=e.message)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": e.code,
                    "message": e.message,
                    "data": e.data}}
        except Exception as e:
            trace(
                "handle_request_error",
                method=method,
                msg_id=msg_id,
                code=-32000,
                message=str(e))
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32000,
                    "message": str(e)}}

    def _forward_to_daemon(
            self, request: JsonMap) -> Optional[JsonMap]:
        """Forward MCP request to the TCP daemon and return response."""
        tid = threading.get_ident()
        trace(
            "forward_to_daemon_enter",
            msg_id=request.get("id"),
            method=request.get("method"))
        try:
            conn, f = self._ensure_daemon_connection(tid)
            try:
                resp = self._forward_over_open_socket(request, conn, f)
                trace(
                    "forward_to_daemon_exit",
                    msg_id=request.get("id"),
                    ok=bool(resp))
                return resp
            except Exception:
                # Retry once with a fresh per-thread connection.
                trace("forward_to_daemon_retry", msg_id=request.get("id"))
                self._close_daemon_connection(tid)
                conn, f = self._ensure_daemon_connection(tid)
                resp = self._forward_over_open_socket(request, conn, f)
                trace(
                    "forward_to_daemon_exit",
                    msg_id=request.get("id"),
                    ok=bool(resp))
                return resp
        except Exception as e:
            trace(
                "forward_to_daemon_error",
                msg_id=request.get("id"),
                error=str(e))
            return _forward_error_response_impl(request, str(e))

    def _ensure_daemon_connection(self, tid: int):
        return _ensure_daemon_connection_impl(
            tid=tid,
            daemon_channels_lock=self._daemon_channels_lock,
            daemon_channels=self._daemon_channels,
            daemon_address=self._daemon_address,
            timeout_sec=settings.DAEMON_TIMEOUT_SEC,
            trace_fn=trace,
            create_connection_fn=socket.create_connection,
        )

    def _close_daemon_connection(self, tid: Optional[int] = None) -> None:
        if tid is None:
            self._close_all_daemon_connections()
            return
        _close_daemon_connection_impl(
            tid=tid,
            daemon_channels_lock=self._daemon_channels_lock,
            daemon_channels=self._daemon_channels,
            trace_fn=trace,
        )

    def _close_all_daemon_connections(self) -> None:
        _close_all_daemon_connections_impl(
            daemon_channels_lock=self._daemon_channels_lock,
            daemon_channels=self._daemon_channels,
            trace_fn=trace,
        )

    def _forward_over_open_socket(
            self, request: JsonMap, conn: object, f: object) -> Optional[JsonMap]:
        return _forward_over_open_socket_impl(
            request=request,
            conn=conn,
            f=f,
            trace_fn=trace,
        )

    def run(self, output_stream: Optional[object] = None) -> None:
        """Standard MCP JSON-RPC loop with encapsulated transport."""
        self._log_debug("Sari MCP Server starting run loop...")
        trace("run_loop_start", workspace_root=self.workspace_root)

        if not self.transport:
            input_stream = getattr(sys.stdin, "buffer", sys.stdin)

            # Use injected stream, or fallback to server property, or finally
            # sys.stdout.buffer
            target_out = output_stream or getattr(
                self, "_original_stdout", None) or getattr(
                sys.stdout, "buffer", sys.stdout)

            wire_format = (os.environ.get("SARI_FORMAT")
                           or "pack").strip().lower()
            # Accept JSONL input for compatibility, but default to
            # Content-Length framing unless explicitly configured.
            self.transport = McpTransport(
                input_stream, target_out, allow_jsonl=True)
            if wire_format == "json":
                self.transport.default_mode = "jsonl"
            else:
                self.transport.default_mode = "content-length"
            trace(
                "transport_initialized",
                wire_format=self.transport.default_mode,
                dev_jsonl=self._dev_jsonl,
                force_content_length=self._force_content_length,
            )

        try:
            while not self._stop.is_set():
                res = self.transport.read_message()
                if res is None:
                    trace("run_loop_eof")
                    break

                req, mode = res
                self._log_debug_request(mode, req)
                trace(
                    "run_loop_received",
                    msg_id=req.get("id"),
                    method=req.get("method"),
                    mode=mode)

                # Attach metadata for response framing matching
                req["_sari_framing_mode"] = mode

                self._enqueue_incoming_request(req)
        except Exception as e:
            self._log_debug(f"CRITICAL in run loop: {e}")
            trace("run_loop_error", error=str(e))
        finally:
            self._drain_pending_requests()
            self._log_debug("Sari MCP Server shutting down...")
            trace("run_loop_shutdown")
            self.shutdown()

    def shutdown(self) -> None:
        """Graceful shutdown of all resources."""
        if self._stop.is_set():
            return
        self._stop.set()
        trace("server_shutdown_start")

        # 1. Stop processing new requests and WAIT for current ones
        try:
            self._executor.shutdown(wait=True, cancel_futures=False)
        except Exception as e:
            self._log_debug(f"Executor shutdown error: {e}")

        # 2. Release only this server's acquired workspace ref.
        # Global registry shutdown here can tear down unrelated sessions.
        try:
            if self.transport and hasattr(self.transport, "close"):
                self.transport.close()
        except Exception:
            pass
        try:
            if self.logger and hasattr(self.logger, "stop"):
                self.logger.stop()
        except Exception:
            pass
        try:
            self._close_all_daemon_connections()
        except Exception:
            pass
        try:
            if self._session_acquired:
                self.registry.release(self.workspace_root)
                self._session_acquired = False
                self._session = None
        except Exception:
            pass
        trace("server_shutdown_done")

    def _worker_loop(self) -> None:
        _worker_loop_impl(
            stop_event=self._stop,
            req_queue=self._req_queue,
            submit_request_for_execution=self._submit_request_for_execution,
            log_debug=self._log_debug,
        )

    def _enqueue_incoming_request(self, req: JsonMap) -> None:
        _enqueue_incoming_request_impl(
            req_queue=self._req_queue,
            req=req,
            emit_queue_overload=self._emit_queue_overload,
            log_debug=self._log_debug,
            trace_fn=trace,
        )

    def _emit_queue_overload(self, req: JsonMap) -> None:
        _emit_queue_overload_impl(
            req=req,
            stdout_lock=self._stdout_lock,
            transport=self.transport,
            log_debug=self._log_debug,
            trace_fn=trace,
        )

    def _submit_request_for_execution(self, req: JsonMap) -> bool:
        return _submit_request_for_execution_impl(
            executor=self._executor,
            handle_and_respond=self._handle_and_respond,
            req=req,
            log_debug=self._log_debug,
        )

    def _drain_pending_requests(self) -> None:
        _drain_pending_requests_impl(
            req_queue=self._req_queue,
            handle_and_respond=self._handle_and_respond,
        )

    def _handle_and_respond(self, req: JsonMap) -> None:
        _handle_and_respond_impl(
            req=req,
            handle_request=self.handle_request,
            force_content_length=self._force_content_length,
            log_debug_response=self._log_debug_response,
            transport=self.transport,
            stdout_lock=self._stdout_lock,
            log_debug=self._log_debug,
            trace_fn=trace,
        )

    def _log_debug(self, message: str) -> None:
        """Log MCP traffic to the structured logger."""
        _log_debug_message_impl(self._debug_enabled, self.struct_logger, message)

    def _sanitize_value(self, value: object, key: str = "") -> object:
        return _sanitize_value_impl(value, self._SENSITIVE_KEYS, key)

    def _log_debug_request(self, mode: str, req: JsonMap) -> None:
        _log_debug_request_impl(
            debug_enabled=self._debug_enabled,
            struct_logger=self.struct_logger,
            mode=mode,
            req=req,
            sanitize_value=self._sanitize_value,
        )

    def _log_debug_response(self, mode: str, resp: JsonMap) -> None:
        _log_debug_response_impl(
            debug_enabled=self._debug_enabled,
            struct_logger=self.struct_logger,
            mode=mode,
            resp=resp,
            sanitize_value=self._sanitize_value,
        )


def main(original_stdout: object = None) -> None:
    # 1. Capture the pure, untouched stdout for MCP communication
    mcp_out = original_stdout or sys.stdout.buffer

    # 2. Immediately redirect global sys.stdout to sys.stderr to isolate side-effects
    # This ensures that even accidental 'print()' calls go to logs, not the
    # protocol.
    sys.stdout = sys.stderr

    server = LocalSearchMCPServer(WorkspaceManager.resolve_workspace_root())

    # 3. Pass only the preserved stream to the server's run loop
    server.run(mcp_out)


if __name__ == "__main__":
    main()
