import asyncio
import json
import unittest
import tempfile
import shutil
from pathlib import Path
from mcp.session import Session
from unittest.mock import MagicMock, AsyncMock

class TestFramingProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader = asyncio.StreamReader()
        self.writer = AsyncMock()
        self.session = Session(self.reader, self.writer)
        self.session.process_request = AsyncMock()

    def feed_data(self, data: bytes):
        self.reader.feed_data(data)

    async def test_newline_in_json(self):
        """Case 1: Newline in JSON body"""
        payload = {"method": "test", "params": {"text": "hello\nworld"}}
        body = json.dumps(payload).encode('utf-8')
        header = f"Content-Length: {len(body)}\r\n\r\n".encode('ascii')
        
        self.feed_data(header + body)
        # We need to stop the infinite loop in handle_connection
        # For testing, we'll just run one iteration
        task = asyncio.create_task(self.session.handle_connection())
        await asyncio.sleep(0.1)
        self.session.running = False
        self.feed_data(b"Content-Length: 0\r\n\r\n") # Trigger break or exit
        
        self.session.process_request.assert_called_with(payload)

    async def test_large_message(self):
        """Case 2: Large message (64KB)"""
        large_text = "a" * 65536
        payload = {"method": "large", "params": {"data": large_text}}
        body = json.dumps(payload).encode('utf-8')
        header = f"Content-Length: {len(body)}\r\n\r\n".encode('ascii')
        
        self.feed_data(header + body)
        task = asyncio.create_task(self.session.handle_connection())
        await asyncio.sleep(0.1)
        self.session.running = False
        self.feed_data(b"Content-Length: 0\r\n\r\n")
        
        self.session.process_request.assert_called_with(payload)

    async def test_sequential_messages(self):
        """Case 4: Sequential messages"""
        p1 = {"method": "msg1"}
        p2 = {"method": "msg2"}
        
        def wrap(p):
            b = json.dumps(p).encode('utf-8')
            return f"Content-Length: {len(b)}\r\n\r\n".encode('ascii') + b
            
        self.feed_data(wrap(p1) + wrap(p2))
        
        task = asyncio.create_task(self.session.handle_connection())
        await asyncio.sleep(0.1)
        self.session.running = False
        self.feed_data(b"Content-Length: 0\r\n\r\n")
        
        self.assertEqual(self.session.process_request.call_count, 2)
        self.session.process_request.assert_any_call(p1)
        self.session.process_request.assert_any_call(p2)

    async def test_invalid_content_length(self):
        """Case 5: Body shorter than Content-Length"""
        body = b'{"msg": "short"}'
        # Content-Length is 100, but body is much shorter
        header = b"Content-Length: 100\r\n\r\n"
        
        self.feed_data(header + body)
        task = asyncio.create_task(self.session.handle_connection())
        await asyncio.sleep(0.1)
        
        # Should be waiting for more data, not called process_request yet
        self.session.process_request.assert_not_called()
        task.cancel()

    async def test_send_json_framing(self):
        """Review 2: Outgoing framing check"""
        payload = {"result": "ok"}
        await self.session.send_json(payload)
        
        expected_body = json.dumps(payload).encode('utf-8')
        expected_header = f"Content-Length: {len(expected_body)}\r\n\r\n".encode('ascii')
        
        self.writer.write.assert_called_once_with(expected_header + expected_body)

if __name__ == "__main__":
    unittest.main()
