from sari.core.daemon_health import detect_orphan_daemons


class _Proc:
    def __init__(self, pid: int, cmdline: list[str]):
        self.info = {"pid": pid, "cmdline": cmdline}


def test_detect_orphan_daemons_filters_registered_pids(monkeypatch):
    monkeypatch.setattr(
        "sari.core.daemon_health._get_registry_daemon_pids",
        lambda: {100},
    )
    monkeypatch.setattr(
        "sari.core.daemon_health.psutil",
        type(
            "P",
            (),
            {
                "process_iter": staticmethod(
                    lambda *_args, **_kwargs: [
                        _Proc(100, ["python", "-m", "sari.mcp.daemon"]),
                        _Proc(200, ["python", "-m", "sari.mcp.daemon"]),
                        _Proc(300, ["python", "-m", "something_else"]),
                    ]
                )
            },
        )(),
    )

    result = detect_orphan_daemons()
    assert len(result) == 1
    assert result[0]["pid"] == 200
