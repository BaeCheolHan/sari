
import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from sari.mcp.async_server import AsyncLocalSearchMCPServer
from sari.mcp.transport import AsyncMcpTransport

@pytest.mark.asyncio
async def test_async_server_init():
    mock_db = MagicMock()
    mock_indexer = MagicMock()
    server = AsyncLocalSearchMCPServer("/tmp/test", db=mock_db, indexer=mock_indexer)
    assert isinstance(server._req_queue, asyncio.Queue)
    assert not server._worker.is_alive()

@pytest.mark.asyncio
async def test_async_server_worker_loop():
    mock_db = MagicMock()
    mock_indexer = MagicMock()
    server = AsyncLocalSearchMCPServer("/tmp/test", db=mock_db, indexer=mock_indexer)
    
    # Mock handle_request to return a predictable response
    server.handle_request = MagicMock(return_value={"jsonrpc": "2.0", "result": "ok", "id": 1})
    
    # Mock transport
    mock_transport = AsyncMock(spec=AsyncMcpTransport)
    server._async_transport = mock_transport
    
    # Start worker loop
    worker_task = asyncio.create_task(server._worker_loop())
    
    # Put a request
    req = {"jsonrpc": "2.0", "method": "ping", "id": 1}
    await server._req_queue.put(req)
    
    # Wait a bit for processing
    await asyncio.sleep(0.1)
    
    # Verify handle_request called in executor (implicitly checked by it being called)
    server.handle_request.assert_called_once_with(req)
    
    # Verify transport write
    mock_transport.write_message.assert_called_once()
    args, kwargs = mock_transport.write_message.call_args
    assert args[0] == {"jsonrpc": "2.0", "result": "ok", "id": 1}
    
    # Cleanup
    server._stop.set()
    await server._req_queue.put(None) # Unblock get() 
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

@pytest.mark.asyncio
async def test_async_server_run_flow():
    """
    Test the full run flow with mocked transport reading/writing.
    """
    mock_db = MagicMock()
    mock_indexer = MagicMock()
    server = AsyncLocalSearchMCPServer("/tmp/test", db=mock_db, indexer=mock_indexer)
    
    # Mock transport creation
    with patch("sari.mcp.async_server.AsyncMcpTransport") as MockTransportCls, \
         patch("asyncio.get_running_loop") as mock_get_loop:
         
        # Setup loop mocks for pipes
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop
        mock_loop.connect_read_pipe = AsyncMock()
        mock_loop.connect_write_pipe = AsyncMock(return_value=(MagicMock(), MagicMock()))
        
        # Setup transport instance mock
        mock_transport = AsyncMock()
        MockTransportCls.return_value = mock_transport
        
        # Simulate one message then None (EOF)
        mock_transport.read_message.side_effect = [
            ({"jsonrpc": "2.0", "method": "ping", "id": 1}, "jsonl"),
            None
        ]
        
        # Run server
        # It should exit after read_message returns None
        await server.run()
        
        # Verify transport behavior
        assert mock_transport.read_message.call_count == 2

