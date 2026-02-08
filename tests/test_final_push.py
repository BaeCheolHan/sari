import pytest
import os
import sys
import asyncio
from unittest.mock import MagicMock, patch

# Mock dependencies
sys.modules['psutil'] = MagicMock()
sys.modules['watchdog'] = MagicMock()
sys.modules['watchdog.observers'] = MagicMock()
sys.modules['watchdog.events'] = MagicMock()

def test_coverage_final_push():
    # 1. sari.main
    import sari.main
    with patch('sys.argv', ['sari', '--help']):
        try: sari.main.main()
        except SystemExit: pass

    # 2. sari.mcp.daemon
    from sari.mcp.daemon import SariDaemon
    daemon = SariDaemon("127.0.0.1", 47779)
    assert daemon.host == "127.0.0.1"

    # 3. sari.mcp.session
    from sari.mcp.session import Session
    s = Session(MagicMock(), MagicMock())
    assert s.running is True

    # 4. sari.core.main
    import sari.core.main
    # If there's a main class or function, call it
    if hasattr(sari.core.main, 'LocalSearchCore'):
        c = sari.core.main.LocalSearchCore(MagicMock())

    # 5. sari.mcp.tools.call_graph
    from sari.mcp.tools.call_graph import execute_call_graph
    db = MagicMock()
    db.search_symbols.return_value = []
    execute_call_graph({"symbol": "test"}, db, ["/tmp"])

    # 6. sari.core.cjk
    from sari.core.cjk import lindera_available
    lindera_available()

    # 7. sari.core.db.schema
    from sari.core.db.schema import init_schema
    init_schema(MagicMock())

def test_indexer_advanced(tmp_path):
    from sari.core.indexer.main import Indexer
    cfg = MagicMock()
    cfg.workspace_roots = [str(tmp_path)]
    cfg.max_depth = 30
    
    mock_settings = MagicMock()
    mock_settings.MAX_DEPTH = 30
    mock_settings.INDEX_MEM_MB = 1024
    mock_settings.INDEX_WORKERS = 2
    mock_settings.get_int.side_effect = lambda key, default: default
    cfg.settings = mock_settings
    
    db = MagicMock()
    with patch('sari.core.db.storage.GlobalStorageManager.get_instance'):
        indexer = Indexer(cfg, db, settings_obj=mock_settings)
        indexer.scan_once()
        # Call some internal methods for coverage
        # indexer._retry_failed_tasks() # Removed in V26
        # indexer.get_queue_depths() # Removed

def test_session_async():
    from sari.mcp.session import Session
    reader = MagicMock()
    writer = MagicMock()
    # Mock reader.readline to return empty (EOF)
    reader.readline = MagicMock(return_value=asyncio.Future())
    reader.readline.return_value.set_result(b"")
    
    s = Session(reader, writer)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(s.handle_connection())
    loop.close()
    assert s.running is False
