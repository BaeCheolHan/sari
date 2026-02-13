from sari.core.http_error_feed import build_errors_payload, parse_log_line_ts


class _Sink:
    def __init__(self, rows):
        self._rows = rows

    def warnings_recent(self):
        return list(self._rows)

    def warning_counts(self):
        return {"W": len(self._rows)}


def test_parse_log_line_ts_supports_millis_and_seconds():
    assert parse_log_line_ts("2026-02-13 10:11:12,123 ERROR boom") > 0
    assert parse_log_line_ts("2026-02-13 10:11:12 ERROR boom") > 0
    assert parse_log_line_ts("not-a-log-line") == 0.0


def test_build_errors_payload_source_filtering():
    sink = _Sink([{"reason_code": "W1", "ts": 9999999999.0}])

    payload_log = build_errors_payload(
        source="log",
        warning_sink_obj=sink,
        read_log_entries=lambda _limit: [{"text": "E1", "ts": 9999999999.0}],
        status_warning_counts_provider=lambda: {"A": 1},
    )
    assert payload_log["warnings_recent"] == []
    assert payload_log["log_errors"] == ["E1"]

    payload_warning = build_errors_payload(
        source="warning",
        warning_sink_obj=sink,
        read_log_entries=lambda _limit: [{"text": "E1", "ts": 9999999999.0}],
        status_warning_counts_provider=lambda: {"A": 1},
    )
    assert payload_warning["warnings_recent"]
    assert payload_warning["log_errors"] == []
