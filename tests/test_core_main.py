import pytest
from unittest.mock import MagicMock, patch
from sari.core.main import _resolve_http_host, _resolve_version, _write_server_info


def test_resolve_http_host():
    assert _resolve_http_host("127.0.0.1", False) == "127.0.0.1"
    assert _resolve_http_host("localhost", False) == "localhost"
    with pytest.raises(SystemExit):
        _resolve_http_host("8.8.8.8", False)
    assert _resolve_http_host("8.8.8.8", True) == "8.8.8.8"


def test_resolve_version():
    assert isinstance(_resolve_version(), str)


def test_write_server_info(tmp_path):
    _write_server_info(str(tmp_path), "127.0.0.1", 47777, 47777)
    info_file = tmp_path / ".codex" / "tools" / "sari" / "data" / "server.json"
    assert info_file.exists()


@patch('sari.core.main.serve_forever')
@patch('sari.core.main.Indexer')
@patch('sari.core.main.LocalSearchDB')
@patch('sari.core.main.Config.load')
@patch('sari.core.workspace.WorkspaceManager.resolve_workspace_root')
def test_core_main_flow(
        mock_resolve,
        mock_config,
        mock_db,
        mock_indexer,
        mock_serve,
        tmp_path):
    mock_resolve.return_value = str(tmp_path)
    mock_config.return_value = MagicMock(
        db_path=str(
            tmp_path / "test.db"),
        http_api_port=47777,
        http_api_host="127.0.0.1")
    mock_serve.return_value = (MagicMock(), 47777)

    # sari.core.main.main() has a while loop, we need to stop it
    with patch('time.sleep', side_effect=[None, InterruptedError]):
        try:
            from sari.core.main import main
            # To avoid the while loop running forever, we can patch the Event
            # class
            with patch('threading.Event') as mock_event_class:
                mock_event = MagicMock()
                mock_event_class.return_value = mock_event
                mock_event.is_set.side_effect = [False, True]
                main()
        except Exception:
            pass

    assert mock_serve.called
