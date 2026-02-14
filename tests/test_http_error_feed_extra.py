from sari.core.http_error_feed import build_errors_payload


class _Sink:
    def warnings_recent(self):
        return [{"reason_code": "W1", "ts": 0}]

    def warning_counts(self):
        return {"W1": 1}


def test_build_errors_payload_coerces_limit_and_since_and_source():
    out = build_errors_payload(
        limit="bad",
        source="unknown",
        since_sec="bad",
        warning_sink_obj=_Sink(),
        read_log_entries=lambda _limit: [{"text": "E1", "ts": 1.0}],
        status_warning_counts_provider=lambda: {"A": 1},
    )
    assert out["limit"] == 50
    assert out["source"] == "all"
    assert out["since_sec"] == 0
    assert out["warnings_recent"]
    assert out["log_errors"] == ["E1"]


def test_build_errors_payload_clamps_limit_and_since():
    out = build_errors_payload(
        limit=9999,
        source="all",
        since_sec=999999999,
        warning_sink_obj=_Sink(),
        read_log_entries=lambda _limit: [],
        status_warning_counts_provider=lambda: {},
    )
    assert out["limit"] == 200
    assert out["since_sec"] == 86400 * 365
