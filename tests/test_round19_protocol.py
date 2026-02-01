import unittest
import json
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from mcp.session import Session
from mcp.registry import Registry

class TestRound19Protocol(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = Registry.get_instance()
        # Clear registry for clean test
        self.registry._states = {}

    async def test_mcp_unknown_method(self):
        """Verify that unknown methods return Method not found error."""
        reader = asyncio.StreamReader()
        writer = MagicMock()
        writer.drain = AsyncMock()
        session = Session(reader, writer)
        
        # Mock shared_state and server
        mock_server = MagicMock()
        # handle_request should return the error response for unknown method
        mock_server.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "error": {"code": -32601, "message": "Method not found"}
        }
        session.shared_state = MagicMock()
        session.shared_state.server = mock_server
        
        request = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "unknown/method",
            "params": {}
        }
        
        await session.process_request(request)
        
        # Check that writer.write was called
        self.assertTrue(writer.write.called)
        called_args = writer.write.call_args[0][0].decode()
        body = called_args.split("\r\n\r\n")[1]
        data = json.loads(body)
        self.assertEqual(data["error"]["code"], -32601)

    def test_registry_workspace_binding(self):
        """Verify Registry returns separate state for different workspaces."""
        state1 = self.registry.get_or_create("/path/to/ws1")
        state2 = self.registry.get_or_create("/path/to/ws2")
        
        self.assertNotEqual(state1, state2)
        self.assertEqual(state1.ref_count, 1)
        
        # Re-fetch same ws
        state1_again = self.registry.get_or_create("/path/to/ws1")
        self.assertEqual(state1, state1_again)
        self.assertEqual(state1.ref_count, 2)

    async def test_session_initialization_flow(self):
        """Verify full initialization flow binds to workspace."""
        reader = asyncio.StreamReader()
        writer = MagicMock()
        writer.drain = AsyncMock()
        session = Session(reader, writer)
        
        # Mock WorkspaceManager.resolve_workspace_root
        with patch("mcp.session.WorkspaceManager.resolve_workspace_root", return_value="/mock/ws"):
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"rootUri": "file:///mock/ws"}
            }
            
            await session.handle_initialize(init_request)
            
            self.assertEqual(session.workspace_root, "/mock/ws")
            self.assertIsNotNone(session.shared_state)
            # Ref count check (get_or_create adds 1)
            self.assertGreaterEqual(session.shared_state.ref_count, 1)

if __name__ == "__main__":
    unittest.main()
