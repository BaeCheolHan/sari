import socket
import threading

from sari.core.db.main import LocalSearchDB
from sari.core.health import SariDoctor
from sari.core.workspace import WorkspaceManager


def test_health_check_db_uses_current_db_api(monkeypatch, tmp_path):
    db_path = tmp_path / "index.db"
    db = LocalSearchDB(str(db_path))
    db.close()

    monkeypatch.setattr(
        WorkspaceManager,
        "get_global_db_path",
        staticmethod(lambda: db_path),
    )

    doc = SariDoctor(workspace_root=str(tmp_path))
    doc.check_db()

    db_access_fail = [
        r for r in doc.results if r["name"] == "DB Access" and not r["passed"]
    ]
    assert not db_access_fail
    assert any(r["name"] == "DB FTS5 Support" for r in doc.results)


def test_health_network_check_uses_socket_probe(monkeypatch):
    def _ok_connect(addr, timeout=0):
        class _Sock:
            def close(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Sock()

    monkeypatch.setattr(socket, "create_connection", _ok_connect)

    doc = SariDoctor()
    assert doc.check_network() is True
    assert any(r["name"] == "Network Check" and r["passed"] for r in doc.results)


def test_health_check_daemon_rejects_non_sari_listener(monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()

    stop_evt = threading.Event()

    def _serve_once() -> None:
        while not stop_evt.is_set():
            try:
                listener.settimeout(0.1)
                conn, _ = listener.accept()
                conn.close()
            except TimeoutError:
                continue
            except OSError:
                return

    th = threading.Thread(target=_serve_once, daemon=True)
    th.start()

    monkeypatch.setattr("sari.core.health.resolve_daemon_address", lambda _ws=None: (host, int(port)))

    doc = SariDoctor(workspace_root="/tmp/fake-ws")
    try:
        assert doc.check_daemon() is False
        assert any(r["name"] == "Sari Daemon" and not r["passed"] for r in doc.results)
    finally:
        stop_evt.set()
        listener.close()
