
import os
import time
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
from threading import Timer

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    # Dummy classes for safe definition
    class FileSystemEventHandler: pass
    class Observer: pass

try:
    from .queue_pipeline import FsEvent, FsEventKind
except Exception:
    from queue_pipeline import FsEvent, FsEventKind

class DebouncedEventHandler(FileSystemEventHandler):
    """Handles events with debounce to prevent duplicate indexing on save."""
    def __init__(self, callback: Callable[[str], None], debounce_seconds: float = 1.0, logger=None):
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.logger = logger
        self._timers = {}
        self._lock = threading.Lock()
        self._pending_events: Dict[str, FsEvent] = {}

    def on_any_event(self, event):
        if event.is_directory:
            return
        
        # We care about Created, Modified, Moved, Deleted
        # watchdog event types: 'created', 'deleted', 'modified', 'moved'
        
        evt_kind = None
        if event.event_type == 'created':
            evt_kind = FsEventKind.CREATED
        elif event.event_type == 'modified':
            evt_kind = FsEventKind.MODIFIED
        elif event.event_type == 'deleted':
            evt_kind = FsEventKind.DELETED
        elif event.event_type == 'moved':
            evt_kind = FsEventKind.MOVED

        if not evt_kind:
            return

        key = event.src_path
        fs_event = FsEvent(kind=evt_kind, path=event.src_path,
                           dest_path=getattr(event, 'dest_path', None),
                           ts=time.time())

        with self._lock:
            if key in self._timers:
                self._timers[key].cancel()
            self._pending_events[key] = fs_event
            t = Timer(self.debounce_seconds, self._trigger, args=[key])
            self._timers[key] = t
            t.start()

    def _trigger(self, path: str):
        with self._lock:
            if path in self._timers:
                del self._timers[path]
            fs_event = self._pending_events.pop(path, None)
        if not fs_event:
            return
        try:
           self.callback(fs_event)
        except Exception as e:
            if self.logger:
                self.logger.log_error(f"Watcher callback failed for {path}: {e}")

class FileWatcher:
    def __init__(self, paths: List[str], on_change_callback: Callable[[FsEvent], None], logger=None):
        self.paths = paths
        self.callback = on_change_callback
        self.logger = logger
        self.observer = None
        self._running = False
        self._monitor_thread = None
        self._stop_event = threading.Event()

    def start(self):
        if not HAS_WATCHDOG:
            if self.logger:
                self.logger.log_info("Watchdog not installed. Skipping real-time monitoring.")
            return

        if self._running:
            return

        self.observer = Observer()
        handler = DebouncedEventHandler(self.callback, logger=self.logger)
        
        started_any = False
        for p in self.paths:
            if os.path.exists(p):
                try:
                    self.observer.schedule(handler, p, recursive=True)
                    started_any = True
                except Exception as e:
                    if self.logger:
                        self.logger.log_error(f"Failed to watch path {p}: {e}")
        
        if started_any:
            try:
                self.observer.start()
                self._running = True
                self._start_monitor()
                if self.logger:
                    self.logger.log_info(f"Watcher started on: {self.paths}")
            except Exception as e:
                if self.logger:
                    self.logger.log_error(f"Failed to start observer: {e}")

    def stop(self):
        self._stop_event.set()
        if self.observer and self._running:
            self.observer.stop()
            self.observer.join()
            self._running = False
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)

    def _start_monitor(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _restart_observer(self):
        try:
            if self.observer:
                try:
                    self.observer.stop()
                    self.observer.join(timeout=1.0)
                except Exception:
                    pass
            self.observer = Observer()
            handler = DebouncedEventHandler(self.callback, logger=self.logger)
            started_any = False
            for p in self.paths:
                if os.path.exists(p):
                    try:
                        self.observer.schedule(handler, p, recursive=True)
                        started_any = True
                    except Exception as e:
                        if self.logger:
                            self.logger.log_error(f"Failed to watch path {p}: {e}")
            if started_any:
                self.observer.start()
                self._running = True
                if self.logger:
                    self.logger.log_info("Watcher restarted.")
            else:
                if self.logger:
                    self.logger.log_error("Watcher restart failed: no valid paths.")
                self._running = False
        except Exception as e:
            if self.logger:
                self.logger.log_error(f"Watcher restart failed: {e}")
            self._running = False

    def _monitor_loop(self):
        try:
            interval = float(os.environ.get("DECKARD_WATCHER_MONITOR_SECONDS", "10"))
        except Exception:
            interval = 10.0
        while not self._stop_event.is_set():
            time.sleep(max(1.0, interval))
            if self._stop_event.is_set():
                break
            if self.observer and not self.observer.is_alive() and self._running:
                if self.logger:
                    self.logger.log_error("Watcher observer died; restarting.")
                self._restart_observer()
