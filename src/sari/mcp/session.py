# ruff: noqa: E402
import json
import logging
import asyncio
import inspect
import os
import urllib.parse
from uuid import uuid4
from typing import Dict,  Optional
from .workspace_registry import Registry, SharedState
from sari.core.settings import settings
from sari.mcp.trace import trace

_SARI_VERSION = settings.VERSION
_SARI_PROTOCOL_VERSION = "2025-11-25"
from sari.core.workspace import WorkspaceManager
from sari.core.server_registry import ServerRegistry

logger = logging.getLogger(__name__)

def _boot_id() -> str:
    return (os.environ.get("SARI_BOOT_ID") or "").strip()

class Session:
    """
    Handles a single client connection.
    Parses JSON-RPC, manages workspace binding via Registry.
    """
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.workspace_root: Optional[str] = None
        self.shared_state: Optional[SharedState] = None
        self.registry = Registry.get_instance()
        self.running = True
        self._preinit_server = None
        self.connection_id = str(uuid4())
        trace("session_init", has_writer=bool(writer))

    def _get_preinit_server(self):
        if self._preinit_server is None:
            from sari.mcp.server import LocalSearchMCPServer
            workspace_root = WorkspaceManager.resolve_workspace_root()
            self._preinit_server = LocalSearchMCPServer(workspace_root, start_worker=False)
        return self._preinit_server

    async def handle_connection(self):
        trace("session_handle_connection_start")
        try:
            while self.running:
                try:
                    # 1. Read first line to detect framing mode
                    line = await self.reader.readline()
                    if not line: # EOF
                        self.running = False
                        break

                    line_str = line.decode("utf-8").strip()
                    if not line_str:
                        continue

                    request_str = ""
                    if line_str.startswith("{"):
                        # JSONL mode: First line is the JSON body
                        request_str = line_str
                        trace("session_detect_jsonl")
                    elif "content-length:" in line_str.lower():
                        # Content-Length mode: Parse headers
                        headers = {}
                        # Parse first header line
                        parts = line_str.split(":", 1)
                        if len(parts) == 2:
                            headers[parts[0].strip().lower()] = parts[1].strip()
                        
                        # Read remaining headers
                        while True:
                            h_line = await self.reader.readline()
                            if not h_line: # EOF mid-header
                                return 
                            h_str = h_line.decode("utf-8").strip()
                            if not h_str: # End of headers
                                break
                            if ":" in h_str:
                                k, v = h_str.split(":", 1)
                                headers[k.strip().lower()] = v.strip()
                        
                        try:
                            content_length = int(headers.get("content-length", 0))
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid Content-Length: {headers.get('content-length')}")
                            trace("session_invalid_content_length", value=headers.get("content-length"))
                            continue

                        if content_length <= 0:
                            continue

                        body = await self.reader.readexactly(content_length)
                        if not body or len(body) != content_length:
                            logger.warning("Incomplete body read")
                            trace("session_incomplete_body", expected=content_length, actual=len(body) if body else 0)
                            break
                        request_str = body.decode("utf-8")
                        trace("session_read_framed", bytes=content_length)
                    else:
                        # Unknown line, skip but don't crash
                        logger.warning(f"Unknown input line in session: {line_str!r}")
                        trace("session_unknown_line", line_preview=line_str[:120])
                        continue

                    if not request_str:
                        continue

                    try:
                        request = json.loads(request_str)
                        trace("session_request_parsed", keys=sorted(list(request.keys())) if isinstance(request, dict) else type(request).__name__)
                        await self.process_request(request)
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON received: {e}")
                        trace("session_json_decode_error", error=str(e))
                        await self.send_error(None, -32700, "Parse error")
                    except Exception as e:
                        logger.error(f"Error processing request logic: {e}", exc_info=True)
                        trace("session_process_error", error=str(e))
                        await self.send_error(None, -32603, str(e))

                except (asyncio.IncompleteReadError, ConnectionResetError):
                    logger.info("Connection closed by client (IncompleteRead/Reset)")
                    trace("session_connection_closed")
                    self.running = False
                    break
                except Exception as e:
                    # CRITICAL: Do not let unhandled exceptions kill the loop
                    logger.error(f"Unhandled transport error: {e}", exc_info=True)
                    trace("session_transport_error", error=str(e))
                    # Try to send error if writer is open, but don't crash if it fails
                    try:
                        await self.send_error(None, -32603, "Internal Transport Error")
                    except Exception:
                        pass
                    # Continue loop - do not break

        except Exception as outer_e:
             logger.critical(f"Fatal session error: {outer_e}", exc_info=True)
             trace("session_fatal_error", error=str(outer_e))
        finally:
            self.cleanup()
            try:
                res = self.writer.close()
                if inspect.isawaitable(res):
                    await res
            except Exception:
                pass
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
            trace("session_handle_connection_end")

    async def process_request(self, request: Dict[str, object]):
        method = request.get("method")
        params = request.get("params", {})
        msg_id = request.get("id")
        trace("session_process_request", method=method, msg_id=msg_id, has_shared_state=bool(self.shared_state))

        if self.workspace_root:
            self.registry.touch_workspace(self.workspace_root)

        if method == "sari/identify":
            draining = False
            boot_id = _boot_id()
            latest_info = None
            try:
                reg = ServerRegistry()
                if boot_id:
                    info = reg.get_daemon(boot_id) or {}
                    draining = bool(info.get("draining"))
                
                # Fetch latest non-draining daemon for this workspace
                # (Simple version for Phase 0: just find any latest non-draining daemon)
                daemons = reg.get_active_daemons()
                if daemons:
                    # Sort by version and start time
                    daemons.sort(key=lambda x: (x.get("version", ""), x.get("start_ts", 0)), reverse=True)
                    latest = daemons[0]
                    latest_info = {
                        "host": latest.get("host"),
                        "port": latest.get("port"),
                        "bootId": latest.get("boot_id"),
                        "version": latest.get("version")
                    }
            except Exception:
                draining = False

            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "name": "sari",
                    "version": _SARI_VERSION,
                    "protocolVersion": _SARI_PROTOCOL_VERSION,
                    "bootId": boot_id,
                    "draining": draining,
                    "latest": latest_info
                },
            }
            await self.send_json(response)
            return
        if method == "initialize":
            await self.handle_initialize(request)
        elif method == "initialized":
            # Just forward to server if bound
            if self.shared_state:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    self.shared_state.server.handle_initialized,
                    params
                )
        elif method == "shutdown":
            # Respond to shutdown but keep connection open for exit
            response = {"jsonrpc": "2.0", "id": msg_id, "result": None}
            await self.send_json(response)
        elif method == "exit":
            self.running = False
        else:
            # Forward other requests to the bound server
            if not self.shared_state:
                if method in {"tools/list", "prompts/list", "resources/list", "resources/templates/list", "roots/list", "ping"}:
                    if msg_id is None:
                        return
                    if method == "tools/list":
                        tools = self._get_preinit_server().list_tools()
                        await self.send_json({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}})
                        return
                    if method == "prompts/list":
                        await self.send_json({"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}})
                        return
                    if method == "resources/list":
                        await self.send_json({"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}})
                        return
                    if method == "resources/templates/list":
                        await self.send_json({"jsonrpc": "2.0", "id": msg_id, "result": {"resourceTemplates": []}})
                        return
                    if method == "roots/list":
                        roots = self._get_preinit_server().list_roots()
                        await self.send_json({"jsonrpc": "2.0", "id": msg_id, "result": {"roots": roots}})
                        return
                    if method == "ping":
                        await self.send_json({"jsonrpc": "2.0", "id": msg_id, "result": {}})
                        return
                await self.send_error(msg_id, -32002, "Server not initialized. Send 'initialize' first.")
                trace("session_not_initialized", method=method, msg_id=msg_id)
                return

            # Execute in thread pool to not block async loop
            # Since LocalSearchMCPServer is synchronous
            forwarded_request = dict(request)
            if method == "tools/call" and isinstance(params, dict):
                forwarded_params = dict(params)
                raw_args = forwarded_params.get("arguments")
                args = dict(raw_args) if isinstance(raw_args, dict) else {}
                args["connection_id"] = self.connection_id
                forwarded_params["arguments"] = args
                forwarded_request["params"] = forwarded_params

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self.shared_state.server.handle_request,
                forwarded_request
            )

            if response:
                await self.send_json(response)
                trace("session_response_sent", msg_id=msg_id, method=method)

    async def handle_initialize(self, request: Dict[str, object]):
        params = request.get("params", {})
        msg_id = request.get("id")
        trace("session_handle_initialize_enter", msg_id=msg_id, params_keys=sorted(list(params.keys())))

        boot_id = _boot_id()
        if boot_id:
            try:
                info = ServerRegistry().get_daemon(boot_id) or {}
                if info.get("draining"):
                    await self.send_error(msg_id, -32001, "Server is draining. Reconnect to the latest daemon.")
                    self.running = False
                    return
            except Exception:
                pass

        root_uri = params.get("rootUri") or params.get("rootPath")
        if not root_uri:
            # Fallback for clients that omit rootUri/rootPath
            root_uri = WorkspaceManager.resolve_workspace_root()
        trace("session_handle_initialize_root_uri", root_uri=root_uri)

        if root_uri.startswith("file://"):
            parsed = urllib.parse.urlparse(root_uri)
            if parsed.netloc and parsed.netloc not in {"localhost", "127.0.0.1", "::1"}:
                await self.send_error(msg_id, -32000, "Unsupported file URI host")
                return
            workspace_root = urllib.parse.unquote(parsed.path)
        else:
            workspace_root = root_uri
            
        workspace_root = WorkspaceManager.normalize_path(workspace_root)
        trace("session_handle_initialize_workspace_root", workspace_root=workspace_root)

        # If already bound to a different workspace, release it
        if self.workspace_root and self.workspace_root != workspace_root:
            self.registry.release(self.workspace_root)
            self.shared_state = None

        self.workspace_root = workspace_root
        persist_flag = params.get("sariPersist") or params.get("persist")
        persist = bool(persist_flag) or str(os.environ.get("SARI_PERSIST_WORKSPACE", "")).strip().lower() in {"1", "true", "yes", "on"}
        self.shared_state = self.registry.get_or_create(self.workspace_root, persistent=persist)
        self.registry.touch_workspace(self.workspace_root)
        trace("session_handle_initialize_bound", workspace_root=self.workspace_root)

        # Record workspace mapping in global registry so other clients can find us
        if not boot_id:
            try:
                sockname = self.writer.get_extra_info("sockname") or ("127.0.0.1", 0)
                host = str(sockname[0] or "127.0.0.1")
                port = int(sockname[1] or 0)
                if port > 0:
                    boot_id = f"legacy-{os.getpid()}-{port}"
                    ServerRegistry().register_daemon(boot_id, host, port, os.getpid(), version=_SARI_VERSION)
            except Exception:
                boot_id = ""

        if boot_id:
            try:
                daemon_info = ServerRegistry().get_daemon(boot_id) or {}
                h_port = daemon_info.get("http_port")
                h_host = daemon_info.get("http_host")
                ServerRegistry().set_workspace(workspace_root, boot_id, http_port=h_port, http_host=h_host)
            except Exception:
                try:
                    ServerRegistry().set_workspace(workspace_root, boot_id)
                except Exception:
                    pass

        # Delegate specific initialize logic to the server instance
        # We need to construct the result based on server's response
        # LocalSearchMCPServer.handle_initialize returns the result dict directly
        try:
            result = self.shared_state.server.handle_initialize(params)
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": result
            }
            await self.send_json(response)
            trace("session_handle_initialize_success", msg_id=msg_id)
        except Exception as e:
            # Rollback: release the workspace if initialization failed
            self.registry.release(self.workspace_root)
            self.workspace_root = None
            self.shared_state = None
            trace("session_handle_initialize_error", msg_id=msg_id, error=str(e))
            await self.send_error(msg_id, -32000, str(e))

    async def send_json(self, data: Dict[str, object]):
        body = json.dumps(data).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        res = self.writer.write(header + body)
        if inspect.isawaitable(res):
            await res
        await self.writer.drain()
        trace("session_send_json", msg_id=data.get("id"), has_error="error" in data, bytes=len(body))

    async def send_error(self, msg_id: object, code: int, message: str):
        response = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": code,
                "message": message
            }
        }
        await self.send_json(response)

    def cleanup(self):
        if self.workspace_root:
            self.registry.release(self.workspace_root)
            self.workspace_root = None
            self.shared_state = None
