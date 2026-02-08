import json
import os
import sys
import threading
import queue
import concurrent.futures
import socket
import time
from pathlib import Path
from typing import Any, Dict, Optional, List
from sari.mcp.workspace_registry import Registry
from sari.core.workspace import WorkspaceManager
from sari.core.settings import settings
from sari.mcp.policies import PolicyEngine
from sari.mcp.middleware import PolicyMiddleware, run_middlewares
from sari.mcp.tools.registry import ToolContext, build_default_registry
from sari.mcp.telemetry import TelemetryLogger
from sari.mcp.transport import McpTransport
from sari.core.utils.logging import get_logger
from sari.mcp.trace import trace

try:
    import orjson as _orjson
except Exception:
    _orjson = None

class JsonRpcException(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data

def _json_dumps(obj: Any) -> str:
    if _orjson:
        return _orjson.dumps(obj).decode("utf-8")
    return json.dumps(obj)


MAX_MESSAGE_SIZE = 10 * 1024 * 1024 # 10MB

class LocalSearchMCPServer:
    """
    Modernized MCP Server for Sari.
    Delegates workspace management to WorkspaceRegistry.
    """
    PROTOCOL_VERSION = "2025-11-25"
    SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
    SERVER_NAME = "sari"
    SERVER_VERSION = settings.VERSION
    _SENSITIVE_KEYS = ("token", "secret", "password", "api_key", "apikey", "authorization", "cookie", "key")

    def __init__(self, workspace_root: str, cfg: Any = None, db: Any = None, indexer: Any = None, start_worker: bool = True):
        self.workspace_root = workspace_root
        trace(
            "server_init_start",
            workspace_root=self.workspace_root,
            injected_cfg=bool(cfg),
            injected_db=bool(db),
            injected_indexer=bool(indexer),
            start_worker=start_worker,
        )
        # Keep optional injected handles for backward compatibility with older callers.
        self._injected_cfg = cfg
        self._injected_db = db
        self._injected_indexer = indexer
        self.registry = Registry.get_instance()
        self.policy_engine = PolicyEngine(mode=settings.SEARCH_FIRST_MODE)
        self.logger = TelemetryLogger(WorkspaceManager.get_global_log_dir())
        self.struct_logger = get_logger("sari.mcp.protocol")
        self._tool_registry = build_default_registry()
        self._middlewares = [PolicyMiddleware(self.policy_engine)]
        self._debug_enabled = settings.DEBUG or os.environ.get("SARI_MCP_DEBUG", "0") == "1"
        self._dev_jsonl = (os.environ.get("SARI_DEV_JSONL") or "").strip().lower() in {"1", "true", "yes", "on"}
        self._force_content_length = (os.environ.get("SARI_FORCE_CONTENT_LENGTH") or "").strip().lower() in {"1", "true", "yes", "on"}
        # Add maxsize to prevent memory bloat under heavy load
        self._req_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=settings.get_int("MCP_QUEUE_SIZE", 1000))
        self._stop = threading.Event()
        self._stdout_lock = threading.Lock()
        self.transport = None
        self._session = None
        self._session_acquired = False
        self._daemon_lock = threading.Lock()
        self._daemon_channels_lock = threading.Lock()
        self._daemon_channels: Dict[int, Any] = {}
        # Duplicate assignment removed. Use _debug_enabled from above.
        
        # Daemon proxy is handled by the stdio proxy process, not the MCP server.
        self._proxy_to_daemon = False
        self._daemon_sock = None

        max_workers = int(os.environ.get("SARI_MCP_WORKERS", "4") or 4)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        if start_worker:
            self._worker.start()
        trace("server_init_done", workspace_root=self.workspace_root, proxy_to_daemon=self._proxy_to_daemon)

    def handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        trace(
            "initialize_enter",
            workspace_root=self.workspace_root,
            params_keys=sorted(list(params.keys())),
            has_root_uri=bool(params.get("rootUri") or params.get("rootPath")),
            protocol_version=params.get("protocolVersion"),
            supported_versions=params.get("supportedProtocolVersions"),
        )
        root_uri = params.get("rootUri") or params.get("rootPath")
        workspace_folders = params.get("workspaceFolders", [])
        
        # Primary workspace selection strategy:
        # 1. Use rootUri if provided.
        # 2. Otherwise, use the first workspaceFolder if available.
        # 3. Fallback to CWD (handled in resolve_workspace_root).
        target_uri = root_uri
        if not target_uri and workspace_folders:
            target_uri = workspace_folders[0].get("uri")

        if target_uri:
            # Update workspace if provided by client
            self.workspace_root = WorkspaceManager.resolve_workspace_root(root_uri=target_uri)
            trace("initialize_resolved_workspace", workspace_root=self.workspace_root, target_uri=target_uri)
        
        negotiated_version = self._negotiate_protocol_version(params)
        trace("initialize_negotiated_version", version=negotiated_version)
            
        return {
            "protocolVersion": negotiated_version,
            "serverInfo": {"name": self.SERVER_NAME, "version": self.SERVER_VERSION},
            # Be explicit about supported capability surfaces so strict MCP
            # clients can finish startup without probing unknown methods.
            "capabilities": {
                "tools": {"listChanged": False},
                "prompts": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "roots": {"listChanged": False},
            },
        }

    def _iter_client_protocol_versions(self, params: Dict[str, Any]) -> List[str]:
        versions: List[str] = []
        seen = set()

        def _append(v: Any) -> None:
            if not isinstance(v, str):
                return
            vv = v.strip()
            if not vv or vv in seen:
                return
            seen.add(vv)
            versions.append(vv)

        _append(params.get("protocolVersion"))
        for v in (params.get("supportedProtocolVersions") or []):
            _append(v)
        caps = params.get("capabilities")
        if isinstance(caps, dict):
            for v in (caps.get("protocolVersions") or []):
                _append(v)

        return versions

    def _negotiate_protocol_version(self, params: Dict[str, Any]) -> str:
        client_versions = self._iter_client_protocol_versions(params)
        for v in client_versions:
            if v in self.SUPPORTED_VERSIONS:
                return v

        strict = (os.environ.get("SARI_STRICT_PROTOCOL") or "").strip().lower() in {"1", "true", "yes", "on"}
        if strict and client_versions:
            raise JsonRpcException(
                -32602,
                "Unsupported protocol version",
                data={"supported": sorted(list(self.SUPPORTED_VERSIONS))}
            )

        return self.PROTOCOL_VERSION

    def handle_initialized(self, params: Dict[str, Any]) -> None:
        """Called by client after initialize response is received."""
        # Optional: Start background tasks here if needed
        pass

    def handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name")
        args = params.get("arguments", {})
        cfg = self._injected_cfg

        if self._injected_db is not None and self._injected_indexer is not None:
            db = self._injected_db
            indexer = self._injected_indexer
            roots = list(getattr(cfg, "workspace_roots", []) or [self.workspace_root])
        else:
            if self._session is None:
                self._session = self.registry.get_or_create(self.workspace_root)
                self._session_acquired = True
            session = self._session
            db = getattr(session, "db", None)
            indexer = getattr(session, "indexer", None)
            cfg_data = getattr(session, "config_data", {}) or {}
            roots = list(cfg_data.get("workspace_roots", [self.workspace_root]))
            if db is None:
                raise JsonRpcException(-32000, "tools/call failed: session.db is unavailable")

        ctx = ToolContext(
            db=db,
            engine=getattr(db, "engine", None),
            indexer=indexer,
            roots=roots,
            cfg=cfg,
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

    def list_roots(self) -> List[Dict[str, str]]:
        """Return configured workspace roots as MCP root objects."""
        cfg = None
        try:
            cfg_path = WorkspaceManager.resolve_config_path(self.workspace_root)
            cfg = Config.load(cfg_path, workspace_root_override=self.workspace_root)
        except Exception:
            cfg = None
        config_roots = list(getattr(cfg, "workspace_roots", []) or []) if cfg else []
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
        """
        Make a Pydantic/JSON Schema object more compatible with various LLMs.
        - 'integer' -> 'number' (+ multipleOf: 1)
        - remove 'null' from union type arrays for better compatibility
        """
        from copy import deepcopy
        s = deepcopy(schema)

        def walk(node):
            if not isinstance(node, dict): return node
            t = node.get("type")
            if isinstance(t, str):
                if t == "integer":
                    node["type"] = "number"
                    if "multipleOf" not in node: node["multipleOf"] = 1
            elif isinstance(t, list):
                t2 = [x if x != "integer" else "number" for x in t if x != "null"]
                if not t2: t2 = ["object"]
                node["type"] = t2[0] if len(t2) == 1 else t2
                if "integer" in t or "number" in t2:
                    node.setdefault("multipleOf", 1)
            
            for key in ("properties", "patternProperties", "definitions", "$defs"):
                if key in node and isinstance(node[key], dict):
                    for k, v in list(node[key].items()): node[key][k] = walk(v)
            if "items" in node: node["items"] = walk(node["items"])
            return node
        return walk(s)

    def list_tools(self) -> List[Dict[str, Any]]:
        expose_internal = os.environ.get("SARI_EXPOSE_INTERNAL_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": self._sanitize_for_llm_tools(t.input_schema),
            }
            for t in self._tool_registry.list_tools_raw()
        ]

    def handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Test helper: Handle tools/list request."""
        return {"tools": self.list_tools()}

    def _ensure_initialized(self) -> None:
        """Test helper: Ensure session is initialized."""
        if self._session is None and self._injected_db is None:
            self._session = self.registry.get_or_create(self.workspace_root)
            self._session_acquired = True

    def _tool_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Test helper: Execute status tool."""
        self._ensure_initialized()
        return self.handle_tools_call({"name": "status", "arguments": args})

    def _tool_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Test helper: Execute search tool."""
        self._ensure_initialized()
        return self.handle_tools_call({"name": "search", "arguments": args})

    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        trace(
            "handle_request_enter",
            method=request.get("method"),
            msg_id=request.get("id"),
            proxy_to_daemon=self._proxy_to_daemon,
        )
        if self._proxy_to_daemon:
            resp = self._forward_to_daemon(request)
            if isinstance(resp, dict):
                err = resp.get("error") if isinstance(resp, dict) else None
                if isinstance(err, dict) and err.get("code") == -32002:
                    # Daemon is unreachable; fall back to local stdio handling.
                    self._log_debug("Daemon proxy failed; falling back to local MCP server.")
                    self._proxy_to_daemon = False
                    self._close_all_daemon_connections()
                    trace("daemon_proxy_fallback", error=err)
                else:
                    trace("handle_request_proxy_response", has_error=bool(err), msg_id=request.get("id"))
                    return resp
            else:
                return resp

        method, params, msg_id = request.get("method"), request.get("params", {}), request.get("id")
        if msg_id is None: return None # Ignore notifications for now
        
        try:
            if method == "initialize": result = self.handle_initialize(params)
            elif method == "tools/list": result = {"tools": self.list_tools()}
            elif method == "prompts/list": result = {"prompts": []}
            elif method == "resources/list": result = {"resources": []}
            elif method == "resources/templates/list": result = {"resourceTemplates": []}
            elif method == "roots/list": result = {"roots": self.list_roots()}
            elif method == "tools/call": 
                result = self.handle_tools_call(params)
                if isinstance(result, dict) and result.get("isError"):
                    err = result.get("error", {})
                    return {
                        "jsonrpc": "2.0", 
                        "id": msg_id, 
                        "error": {
                            "code": err.get("code", -32000), 
                            "message": err.get("message", "Unknown tool error"),
                            "data": result
                        }
                    }
            elif method in {"initialized", "notifications/initialized"}: result = {}
            elif method == "ping": result = {}
            else: return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": result}
            trace("handle_request_exit", method=method, msg_id=msg_id, ok=True)
            return resp
        except JsonRpcException as e:
            trace("handle_request_error", method=method, msg_id=msg_id, code=e.code, message=e.message)
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": e.code, "message": e.message, "data": e.data}}
        except Exception as e:
            trace("handle_request_error", method=method, msg_id=msg_id, code=-32000, message=str(e))
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(e)}}

    def _forward_to_daemon(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Forward MCP request to the TCP daemon and return response."""
        tid = threading.get_ident()
        trace("forward_to_daemon_enter", msg_id=request.get("id"), method=request.get("method"))
        try:
            conn, f = self._ensure_daemon_connection(tid)
            try:
                resp = self._forward_over_open_socket(request, conn, f)
                trace("forward_to_daemon_exit", msg_id=request.get("id"), ok=bool(resp))
                return resp
            except Exception:
                # Retry once with a fresh per-thread connection.
                trace("forward_to_daemon_retry", msg_id=request.get("id"))
                self._close_daemon_connection(tid)
                conn, f = self._ensure_daemon_connection(tid)
                resp = self._forward_over_open_socket(request, conn, f)
                trace("forward_to_daemon_exit", msg_id=request.get("id"), ok=bool(resp))
                return resp
        except Exception as e:
            trace("forward_to_daemon_error", msg_id=request.get("id"), error=str(e))
            msg_id = request.get("id")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32002,
                    "message": f"Failed to forward to daemon: {e}. Try 'sari daemon start'."
                }
            } if msg_id is not None else None

    def _ensure_daemon_connection(self, tid: int):
        with self._daemon_channels_lock:
            ch = self._daemon_channels.get(tid)
            if ch is not None:
                trace("daemon_connection_reuse", tid=tid)
                return ch
        trace("daemon_connection_new", tid=tid, daemon_address=getattr(self, "_daemon_address", None))
        conn = socket.create_connection(self._daemon_address, timeout=settings.DAEMON_TIMEOUT_SEC)
        f = conn.makefile("rb")
        with self._daemon_channels_lock:
            self._daemon_channels[tid] = (conn, f)
        return conn, f

    def _close_daemon_connection(self, tid: Optional[int] = None) -> None:
        if tid is None:
            self._close_all_daemon_connections()
            return
        with self._daemon_channels_lock:
            ch = self._daemon_channels.pop(tid, None)
        if not ch:
            return
        trace("daemon_connection_close", tid=tid)
        conn, f = ch
        try:
            f.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    def _close_all_daemon_connections(self) -> None:
        with self._daemon_channels_lock:
            items = list(self._daemon_channels.items())
            self._daemon_channels.clear()
        trace("daemon_connections_close_all", count=len(items))
        for _tid, (conn, f) in items:
            try:
                f.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def _forward_over_open_socket(self, request: Dict[str, Any], conn: Any, f: Any) -> Optional[Dict[str, Any]]:
        body = json.dumps(request).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        conn.sendall(header + body)
        trace("daemon_socket_sent", msg_id=request.get("id"), method=request.get("method"), bytes=len(body))

        headers: Dict[bytes, bytes] = {}
        while True:
            line = f.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                break
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower()] = v.strip()

        content_length = int(headers.get(b"content-length", b"0"))
        if content_length <= 0:
            trace("daemon_socket_no_content", msg_id=request.get("id"))
            return None
        resp_body = f.read(content_length)
        if not resp_body:
            trace("daemon_socket_no_body", msg_id=request.get("id"))
            return None
        resp = json.loads(resp_body.decode("utf-8"))
        trace("daemon_socket_received", msg_id=request.get("id"), bytes=content_length)
        return resp

    def run(self) -> None:
        """Standard MCP JSON-RPC loop with encapsulated transport."""
        self._log_debug("Sari MCP Server starting run loop...")
        trace("run_loop_start", workspace_root=self.workspace_root)
        
        if not self.transport:
            input_stream = getattr(sys.stdin, "buffer", sys.stdin)
            original_stdout = getattr(self, "_original_stdout", None)
            output_stream = getattr(original_stdout, "buffer", None) if original_stdout is not None else None
            if output_stream is None:
                output_stream = getattr(sys.stdout, "buffer", sys.stdout)
            wire_format = (os.environ.get("SARI_FORMAT") or "pack").strip().lower()
            # Accept JSONL input for compatibility, but default to Content-Length framing unless explicitly configured.
            self.transport = McpTransport(input_stream, output_stream, allow_jsonl=True)
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
                trace("run_loop_received", msg_id=req.get("id"), method=req.get("method"), mode=mode)
                
                # Attach metadata for response framing matching
                req["_sari_framing_mode"] = mode
                
                try:
                    # Non-blocking put to avoid hanging the main read loop if workers are slow.
                    # We use a short timeout to handle transient spikes.
                    self._req_queue.put(req, timeout=0.01)
                except queue.Full:
                    msg_id = req.get("id")
                    if msg_id is not None:
                        error_resp = {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "error": {
                                "code": -32003,
                                "message": "Server overloaded: request queue is full. Please try again later."
                            }
                        }
                        mode = req.get("_sari_framing_mode", "content-length")
                        with self._stdout_lock:
                            self.transport.write_message(error_resp, mode=mode)
                    self._log_debug(f"CRITICAL: MCP request queue is full! Dropping request {msg_id}")
                    trace("run_loop_queue_full", msg_id=msg_id)
                except Exception as e:
                    self._log_debug(f"ERROR putting req to queue: {e}")
                    trace("run_loop_queue_error", error=str(e))
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
        
        # 1. Stop processing new requests
        try:
            self._executor.shutdown(wait=True, cancel_futures=False)
        except Exception:
            pass
            
        # 2. Cleanup all workspace resources (DB, Engine)
        try:
            self.registry.shutdown_all()
        except Exception:
            pass
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
            self._close_daemon_connection()
        except Exception:
            pass
        try:
            if self._session_acquired:
                self.registry.release(self.workspace_root)
                self._session_acquired = False
                self._session = None
        except Exception:
            pass

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                req = self._req_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            except Exception as e:
                # Queue access error - log and continue
                self._log_debug(f"Queue access error in worker loop: {e}")
                continue
            
            try:
                self._executor.submit(self._handle_and_respond, req)
            except RuntimeError as e:
                # Executor shutdown - graceful exit
                self._log_debug(f"Executor shutdown during submit: {e}")
                break
            except Exception as e:
                # Unexpected error - log and continue
                self._log_debug(f"Error submitting to executor: {e}")
            finally:
                try:
                    self._req_queue.task_done()
                except Exception:
                    pass


    def _drain_pending_requests(self) -> None:
        while True:
            try:
                req = self._req_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_and_respond(req)
            finally:
                try:
                    self._req_queue.task_done()
                except Exception:
                    pass

    def _handle_and_respond(self, req: Dict[str, Any]) -> None:
        try:
            trace("handle_and_respond_enter", msg_id=req.get("id"), method=req.get("method"))
            resp = self.handle_request(req)
            if resp:
                req_mode = req.get("_sari_framing_mode", "content-length")
                if self._force_content_length and req_mode != "jsonl":
                    mode = "content-length"
                else:
                    mode = req_mode
                self._log_debug_response(mode, resp)
                if self.transport is None:
                    raise RuntimeError("transport is not initialized")
                # Serialize writes to stdout transport to avoid frame interleaving.
                with self._stdout_lock:
                    self.transport.write_message(resp, mode=mode)
                trace("handle_and_respond_sent", msg_id=req.get("id"), mode=mode)
        except Exception as e:
            self._log_debug(f"ERROR in _handle_and_respond: {e}")
            trace("handle_and_respond_error", msg_id=req.get("id"), error=str(e))

    def _log_debug(self, message: str) -> None:
        """Log MCP traffic to the structured logger."""
        if not self._debug_enabled:
            return
        # Use a specific event name for raw string messages
        self.struct_logger.debug("mcp_debug_log", message=message)

    def _sanitize_value(self, value: Any, key: str = "") -> Any:
        key_l = (key or "").lower()
        if any(s in key_l for s in self._SENSITIVE_KEYS):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {k: self._sanitize_value(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(v, key) for v in value[:20]]
        if isinstance(value, str):
            if key_l in {"content", "text", "source", "snippet", "body"}:
                return f"[REDACTED_TEXT len={len(value)}]"
            if len(value) > 200:
                return value[:120] + "...[truncated]"
            return value
        return value

    def _log_debug_request(self, mode: str, req: Dict[str, Any]) -> None:
        if not self._debug_enabled:
            return
        summary: Dict[str, Any] = {
            "id": req.get("id"),
            "method": req.get("method"),
            "mode": mode,
            "keys": sorted([k for k in req.keys() if not str(k).startswith("_")]),
        }
        params = req.get("params") or {}
        if req.get("method") == "tools/call" and isinstance(params, dict):
            args = params.get("arguments") or {}
            summary["tool"] = params.get("name")
            if isinstance(args, dict):
                summary["argument_keys"] = sorted(list(args.keys()))
                summary["arguments"] = {k: self._sanitize_value(v, k) for k, v in args.items()}
        
        # Log as structured event
        self.struct_logger.debug("mcp_request", **summary)

    def _log_debug_response(self, mode: str, resp: Dict[str, Any]) -> None:
        if not self._debug_enabled:
            return
        summary: Dict[str, Any] = {
            "id": resp.get("id"),
            "mode": mode,
            "has_result": "result" in resp,
            "has_error": "error" in resp,
        }
        # Simplify summary logic for response logging
        # We don't want to log generic outbound debug string if we can log structured data
        if "error" in resp and isinstance(resp["error"], dict):
            summary["error"] = self._sanitize_value(resp["error"])
        
        self.struct_logger.debug("mcp_response", **summary)

def main(original_stdout: Any = None) -> None:
    # Use provided stdout or fallback to current sys.stdout
    clean_stdout = original_stdout or sys.stdout
    server = LocalSearchMCPServer(WorkspaceManager.resolve_workspace_root())
    # Ensure the worker loop uses the correct output stream
    server._original_stdout = clean_stdout
    server.run()

if __name__ == "__main__":
    main()
