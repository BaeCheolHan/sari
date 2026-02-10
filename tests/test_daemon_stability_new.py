import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from sari.mcp.daemon import SariDaemon
from sari.core.server_registry import ServerRegistry

@pytest.mark.asyncio
async def test_daemon_duplicate_start_prevention():
    """
    T1: Verify that starting a daemon fails if another one is already registered on the same port.
    """
    host = "127.0.0.1"
    port = 47779
    existing_pid = 12345
    
    # Mock registry to return an existing daemon
    mock_registry = MagicMock(spec=ServerRegistry)
    mock_registry.resolve_daemon_by_endpoint.return_value = {
        "pid": existing_pid,
        "host": host,
        "port": port
    }
    
    with patch("sari.mcp.daemon.ServerRegistry", return_value=mock_registry):
        daemon = SariDaemon(host=host, port=port)
        
        with pytest.raises(SystemExit) as excinfo:
            await daemon.start_async()
        
        assert f"already running on {host}:{port}" in str(excinfo.value)
        assert f"PID: {existing_pid}" in str(excinfo.value)

@pytest.mark.asyncio
async def test_daemon_clean_start_allowed():
    """
    T1+: Verify that a daemon can start if no other instance is registered.
    """
    host = "127.0.0.1"
    port = 48888
    
    mock_registry = MagicMock(spec=ServerRegistry)
    mock_registry.resolve_daemon_by_endpoint.return_value = None
    
    # Mock start_server to return a mock server that doesn't hang
    mock_server = AsyncMock()
    mock_server.serve_forever = AsyncMock(return_value=None)
    mock_server.sockets = [MagicMock()]
    mock_server.sockets[0].getsockname.return_value = (host, port)
    
    with patch("sari.mcp.daemon.ServerRegistry", return_value=mock_registry), \
         patch("asyncio.start_server", return_value=mock_server):
        
        daemon = SariDaemon(host=host, port=port)
        daemon._register_daemon = MagicMock()
        daemon._autostart_workspace = MagicMock()
        daemon._start_heartbeat = MagicMock()
        
        # We need to stop the server from serving forever in the test
        # So we mock serve_forever to return immediately
        mock_server.serve_forever.side_effect = None 

        await asyncio.wait_for(daemon.start_async(), timeout=1.0)
        mock_registry.resolve_daemon_by_endpoint.assert_called_with(host, port)
        daemon._register_daemon.assert_called_once()
        daemon._autostart_workspace.assert_called_once()
        daemon._start_heartbeat.assert_called_once()
        assert mock_registry.resolve_daemon_by_endpoint.call_count == 1
