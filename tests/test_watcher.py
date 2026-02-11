from unittest.mock import MagicMock, patch
from sari.core.watcher import DebouncedEventHandler, _is_git_event, FileWatcher
from sari.core.queue_pipeline import FsEvent, FsEventKind

def test_is_git_event():
    assert _is_git_event(".git/HEAD") is True
    assert _is_git_event("/tmp/repo/.git/index") is True
    assert _is_git_event("/tmp/repo/.git/packed-refs") is True
    assert _is_git_event("/tmp/repo/index") is False
    assert _is_git_event("src/main.py") is False

def test_debounced_event_handler_direct():
    callback = MagicMock()
    handler = DebouncedEventHandler(callback, debounce_seconds=0.01)
    handler._bucket_tokens = 100.0
    
    # Directly call trigger to cover logic without waiting for Timer
    handler._trigger("test.txt")
    # Note: _trigger only calls callback if item was in _pending_events
    # So we need to put it there first
    handler._pending_events["test.txt"] = MagicMock()
    handler._trigger("test.txt")
    callback.assert_called_once()
    assert callback.call_count == 1

def test_debounced_event_handler_on_any_event():
    callback = MagicMock()
    with patch('threading.Timer'):
        handler = DebouncedEventHandler(callback, debounce_seconds=0.01)
        handler._bucket_tokens = 100.0
        event = MagicMock()
        event.is_directory = False
        event.event_type = 'modified'
        event.src_path = "test.txt"
        handler.on_any_event(event)
        assert "test.txt" in handler._pending_events


def test_debounced_event_handler_git_event_without_git_callback_falls_back_to_normal_flow():
    callback = MagicMock()
    with patch('threading.Timer'):
        handler = DebouncedEventHandler(callback, debounce_seconds=0.01, git_callback=None)
        handler._bucket_tokens = 100.0
        event = MagicMock()
        event.is_directory = False
        event.event_type = 'modified'
        event.src_path = "/repo/.git/index"
        event.dest_path = ""
        handler.on_any_event(event)
        assert event.src_path in handler._pending_events


def test_debounced_event_handler_git_event_with_git_callback_defers_to_git_timer():
    callback = MagicMock()
    git_callback = MagicMock()
    with patch('threading.Timer'):
        handler = DebouncedEventHandler(callback, debounce_seconds=0.01, git_callback=git_callback)
        handler._bucket_tokens = 100.0
        event = MagicMock()
        event.is_directory = False
        event.event_type = 'modified'
        event.src_path = "/repo/.git/index"
        event.dest_path = ""
        handler.on_any_event(event)
        assert event.src_path not in handler._pending_events

def test_watcher_start_stop(tmp_path):
    callback = MagicMock()
    watcher = FileWatcher([str(tmp_path)], callback)
    with patch('watchdog.observers.Observer'):
        watcher.start()
        watcher.stop()
    assert watcher._running is False


def test_watcher_dispatch_infers_root(tmp_path):
    callback = MagicMock()
    event_bus = MagicMock()
    watcher = FileWatcher([str(tmp_path)], callback, event_bus=event_bus)
    evt = FsEvent(kind=FsEventKind.MODIFIED, path=str(tmp_path / "a.py"), root="")
    watcher._dispatch_event(evt)
    published_evt = event_bus.publish.call_args[0][1]
    assert published_evt.root != ""
    assert published_evt.root == str(tmp_path)
