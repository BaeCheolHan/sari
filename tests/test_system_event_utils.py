from sari.core.events import EventBus
from sari.core.utils.system import list_sari_processes


def test_event_bus_publish_continues_on_handler_error():
    bus = EventBus()
    seen = []

    def bad_handler(_payload):
        raise RuntimeError("boom")

    def good_handler(payload):
        seen.append(payload)

    bus.subscribe("topic", bad_handler)
    bus.subscribe("topic", good_handler)
    bus.publish("topic", {"k": "v"})

    assert seen == [{"k": "v"}]


def test_list_sari_processes_tolerates_missing_name(monkeypatch):
    class _Proc:
        def __init__(self):
            self.info = {
                "pid": 123,
                "name": None,
                "cmdline": ["python", "-m", "sari"],
                "create_time": 1.0,
                "memory_info": type("M", (), {"rss": 1024 * 1024})(),
            }

    monkeypatch.setattr("sari.core.utils.system.psutil.process_iter", lambda *_args, **_kwargs: [_Proc()])

    procs = list_sari_processes()

    assert isinstance(procs, list)
