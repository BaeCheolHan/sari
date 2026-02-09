"""
Async implementation of LocalSearchMCPServer.
Uses asyncio for main loop and worker, delegating sync logic to executor.
"""
import asyncio
import json
import logging
import os
import sys
import signal
import inspect
from typing import Any, Dict, Optional
from sari.mcp.server import LocalSearchMCPServer
from sari.mcp.transport import AsyncMcpTransport
from sari.core.settings import settings

logger = logging.getLogger(__name__)

class AsyncLocalSearchMCPServer(LocalSearchMCPServer):
    """
    비동기 MCP 서버.
    메인 루프와 워커 루프를 asyncio로 실행하며,
    동기적인 로직(DB 접근 등)은 ThreadPoolExecutor에서 실행합니다.
    """

    def __init__(self, workspace_root: str, cfg: Any = None, db: Any = None, indexer: Any = None):
        # 부모 클래스 초기화 (워커 스레드 시작 안 함)
        super().__init__(workspace_root, cfg, db, indexer, start_worker=False)
        
        # Async-specific state
        self._req_queue = asyncio.Queue(maxsize=settings.get_int("MCP_QUEUE_SIZE", 1000))
        self._stop = asyncio.Event()
        self._async_transport: Optional[AsyncMcpTransport] = None
        
        # Override locks with async primitives if needed?
        # No, handles logic runs in threads, so threading.Lock is actually correct for shared state 
        # accessed from those threads.
        # But _stdout_lock guards transport writes.
        # Async transport writes are awaitable.
        # We need an asyncio.Lock for transport writing to prevent interleaving frames.
        self._async_stdout_lock = asyncio.Lock()

    async def run(self) -> None:
        """Async main loop with improved lifecycle management."""
        self._log_debug("Sari Async MCP Server starting...")
        
        loop = asyncio.get_running_loop()
        
        # 1. Setup Async Transport
        # Use _original_stdout if injected (main.py), else default to sys.stdout
        original_stdout = getattr(self, "_original_stdout", sys.stdout)
        
        # Reader pipe setup
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        
        # Writer pipe setup with flow control
        w_transport, w_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, 
            original_stdout
        )
        writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)
        
        wire_format = (os.environ.get("SARI_FORMAT") or "pack").strip().lower()
        self._async_transport = AsyncMcpTransport(reader, writer, allow_jsonl=True)
        self._async_transport.default_mode = "jsonl" if wire_format == "json" else "content-length"
        
        main_task = asyncio.current_task()
        
        def _request_stop():
            self._log_debug("Termination signal received.")
            self._stop.set()
            if main_task and not main_task.done():
                main_task.cancel()
                
        if os.name != "nt":
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(sig, _request_stop)
                except (NotImplementedError, ValueError):
                    pass
        
        # 2. Start Worker Task
        worker_task = asyncio.create_task(self._worker_loop())
        
        # 3. Main Message Loop
        try:
            while not self._stop.is_set():
                res = await self._async_transport.read_message()
                if res is None:
                    break
                
                req, mode = res
                self._log_debug_request(mode, req)
                req["_sari_framing_mode"] = mode
                
                try:
                    # Enforce queue limit with a small timeout to prevent blocking the main loop
                    await asyncio.wait_for(self._req_queue.put(req), timeout=0.2)
                except asyncio.TimeoutError:
                    msg_id = req.get("id")
                    if msg_id is not None:
                        error_resp = {
                            "jsonrpc": "2.0", 
                            "id": msg_id, 
                            "error": {"code": -32003, "message": "Server overloaded (Queue Full)"}
                        }
                        await self._async_transport.write_message(error_resp, mode=mode)
                    self._log_debug(f"Queue full: dropped request {msg_id}")
                    
        except asyncio.CancelledError:
            self._log_debug("Main loop cancelled.")
        except Exception as e:
            self._log_debug(f"Critical error in async loop: {e}")
        finally:
            self._stop.set()
            
            # Graceful worker shutdown
            worker_task.cancel()
            try:
                await asyncio.wait_for(worker_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            
            self.shutdown() # Synchronous executor shutdown
            
            # Final transport cleanup
            try:
                if self._async_transport:
                    await self._async_transport.close()
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            
            self._log_debug("Sari Async MCP Server stopped.")

    async def _worker_loop(self) -> None:
        """Optimized async worker loop."""
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                req = await self._req_queue.get()
            except asyncio.CancelledError:
                break
            
            try:
                # Offload heavy sync/blocking logic to the executor
                resp = await loop.run_in_executor(self._executor, self.handle_request, req)
                
                if resp:
                    mode = req.get("_sari_framing_mode", "content-length")
                    self._log_debug_response(mode, resp)
                    
                    async with self._async_stdout_lock:
                         if self._async_transport:
                             await self._async_transport.write_message(resp, mode=mode)
            except Exception as e:
                self._log_debug(f"Worker error: {e}")
            finally:
                self._req_queue.task_done()
