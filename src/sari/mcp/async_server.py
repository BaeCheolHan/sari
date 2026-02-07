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
        """Async main loop."""
        self._log_debug("Sari Async MCP Server starting...")
        
        # 1. Setup Async Transport
        loop = asyncio.get_running_loop()
        
        # Setup stdin reader
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        
        # Setup stdout writer
        # Use _original_stdout if injected, else sys.stdout
        # Note: In main.py, _original_stdout is injected.
        original_stdout = getattr(self, "_original_stdout", sys.stdout)
        
        w_transport, w_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, 
            original_stdout
        )
        writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)
        
        wire_format = (os.environ.get("SARI_FORMAT") or "pack").strip().lower()
        self._async_transport = AsyncMcpTransport(reader, writer, allow_jsonl=True)
        self._async_transport.default_mode = "jsonl" if wire_format == "json" else "content-length"
        
        # Capture main task for cancellation
        main_task = asyncio.current_task()
        
        def _signal_handler():
            self._log_debug("Received termination signal")
            self._stop.set()
            if main_task:
                main_task.cancel()
                
        # Register signal handlers for graceful shutdown (prevent zombies)
        # Note: add_signal_handler is not available on Windows
        if os.name != "nt":
            try:
                loop.add_signal_handler(signal.SIGTERM, _signal_handler)
                loop.add_signal_handler(signal.SIGINT, _signal_handler)
            except NotImplementedError:
                pass
        
        # 2. Start Worker Task
        worker_task = asyncio.create_task(self._worker_loop())
        
        # 3. Read Loop
        try:
            while not self._stop.is_set():
                res = await self._async_transport.read_message()
                if res is None:
                    break
                
                req, mode = res
                self._log_debug_request(mode, req)
                req["_sari_framing_mode"] = mode
                
                try:
                    # Async put with timeout not directly supported by Queue.put
                    # But wait_for can do it.
                    await asyncio.wait_for(self._req_queue.put(req), timeout=0.1)
                except asyncio.TimeoutError:
                    # Queue full
                    msg_id = req.get("id")
                    if msg_id is not None:
                        error_resp = {
                            "jsonrpc": "2.0", 
                            "id": msg_id, 
                            "error": {"code": -32003, "message": "Server overloaded"}
                        }
                        await self._async_transport.write_message(error_resp, mode=mode)
                    self._log_debug(f"CRITICAL: Async Queue full, dropped {msg_id}")
                except Exception as e:
                    self._log_debug(f"Error putting to queue: {e}")
                    
        except Exception as e:
            self._log_debug(f"CRITICAL in async run loop: {e}")
        finally:
            self._stop.set()
            # Cancel worker
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
            
            # Flush pending?
            # Creating a new task to flush might be too late if loop closes.
            # Best effort.
            
            self.shutdown() # calls executor shutdown
            
            # Close transport
            try:
                self._async_transport.close()
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            
            self._log_debug("Sari Async MCP Server stopped.")

    async def _worker_loop(self) -> None:
        """Async worker loop consuming requests."""
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                # Wait for request
                req = await self._req_queue.get()
            except asyncio.CancelledError:
                break
            
            try:
                # Run sync logic in thread pool
                # handle_request executes tools, which might use DB (blocking)
                resp = await loop.run_in_executor(self._executor, self.handle_request, req)
                
                if resp:
                    mode = req.get("_sari_framing_mode", "content-length")
                    self._log_debug_response(mode, resp)
                    
                    async with self._async_stdout_lock:
                         if self._async_transport:
                             await self._async_transport.write_message(resp, mode=mode)
            except Exception as e:
                self._log_debug(f"Error in async worker: {e}")
            finally:
                self._req_queue.task_done()
