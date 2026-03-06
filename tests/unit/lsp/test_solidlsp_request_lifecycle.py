"""solidlsp request lifecycle hook 계약을 검증한다."""

from __future__ import annotations

from solidlsp.ls_config import Language
from solidlsp.ls_handler import SolidLanguageServerHandler
from solidlsp.lsp_protocol_handler.server import ProcessLaunchInfo


def _build_handler() -> SolidLanguageServerHandler:
    return SolidLanguageServerHandler(
        process_launch_info=ProcessLaunchInfo(cmd=["dummy"], cwd=".", env={}),
        language=Language.PYTHON,
        determine_log_level=lambda _line: 20,
    )


def test_send_request_reports_lifecycle_success(monkeypatch) -> None:
    handler = _build_handler()
    events: list[tuple[str, str, int, bool | None]] = []
    handler.set_request_lifecycle_hooks(
        on_request_start=lambda method, request_id: events.append(("start", method, int(request_id), None)),
        on_request_end=lambda method, request_id, ok: events.append(("end", method, int(request_id), ok)),
    )

    def _fake_send_payload(_payload) -> None:
        request = handler._pending_requests[1]  # noqa: SLF001
        request.on_result({"ok": True})

    monkeypatch.setattr(handler, "_send_payload", _fake_send_payload)

    result = handler.send_request("workspace/symbol", {"query": "A"})

    assert result == {"ok": True}
    assert events == [
        ("start", "workspace/symbol", 1, None),
        ("end", "workspace/symbol", 1, True),
    ]


def test_cancel_pending_requests_reports_end_once(monkeypatch) -> None:
    handler = _build_handler()
    events: list[tuple[str, str, int, bool | None]] = []
    handler.set_request_lifecycle_hooks(
        on_request_start=lambda method, request_id: events.append(("start", method, int(request_id), None)),
        on_request_end=lambda method, request_id, ok: events.append(("end", method, int(request_id), ok)),
    )

    def _fake_send_payload(_payload) -> None:
        handler._cancel_pending_requests(RuntimeError("boom"))  # noqa: SLF001

    monkeypatch.setattr(handler, "_send_payload", _fake_send_payload)

    try:
        handler.send_request("workspace/symbol", {"query": "A"})
    except Exception:
        pass

    assert events == [
        ("start", "workspace/symbol", 1, None),
        ("end", "workspace/symbol", 1, False),
    ]
