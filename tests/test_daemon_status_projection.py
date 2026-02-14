from sari.core.daemon_status_projection import build_daemon_state_projection


class _Reg:
    def __init__(self, *, by_endpoint=None, by_workspace=None):
        self._by_endpoint = by_endpoint
        self._by_workspace = by_workspace

    def resolve_daemon_by_endpoint(self, _host: str, _port: int):
        return self._by_endpoint

    def resolve_workspace_daemon(self, _workspace_root: str):
        return self._by_workspace


def test_projection_marks_registry_stale_when_socket_dead():
    reg = _Reg(by_endpoint={"host": "127.0.0.1", "port": 47779, "pid": 1234})
    projection = build_daemon_state_projection(
        host="127.0.0.1",
        port=47779,
        workspace_root="/tmp/ws",
        registry=reg,
        socket_probe=lambda _h, _p: False,
        process_probe=lambda _pid: False,
    )
    assert projection["registry_truth"]["ok"] is True
    assert projection["socket_truth"]["ok"] is False
    assert projection["process_truth"]["ok"] is False
    assert projection["final_truth"] == "degraded"
    assert projection["mismatch_reason"] == "registry_stale"


def test_projection_marks_unregistered_live_when_socket_up_process_dead():
    reg = _Reg(by_endpoint=None, by_workspace=None)
    projection = build_daemon_state_projection(
        host="127.0.0.1",
        port=47779,
        workspace_root="/tmp/ws",
        registry=reg,
        socket_probe=lambda _h, _p: True,
        process_probe=lambda _pid: False,
    )
    assert projection["registry_truth"]["ok"] is False
    assert projection["socket_truth"]["ok"] is True
    assert projection["final_truth"] == "degraded"
    assert projection["mismatch_reason"] == "socket_live_without_registry"


def test_projection_marks_running_when_registry_socket_process_align():
    reg = _Reg(by_endpoint={"host": "127.0.0.1", "port": 47779, "pid": 9999})
    projection = build_daemon_state_projection(
        host="127.0.0.1",
        port=47779,
        workspace_root="/tmp/ws",
        registry=reg,
        socket_probe=lambda _h, _p: True,
        process_probe=lambda _pid: True,
    )
    assert projection["final_truth"] == "running"
    assert projection["mismatch_reason"] == ""
