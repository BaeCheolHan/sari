from sari.mcp.stabilization.warning_sink import WarningSink


def test_warning_sink_counts_and_recent_ring():
    sink = WarningSink(max_recent=3)

    sink.warn("A", "x.where")
    sink.warn("B", "y.where")
    sink.warn("A", "z.where", extra={"k": 1})
    sink.warn("C", "c.where")

    counts = sink.warning_counts()
    recent = sink.warnings_recent()

    assert counts["A"] == 2
    assert counts["B"] == 1
    assert counts["C"] == 1
    assert len(recent) == 3
    assert recent[-1]["reason_code"] == "C"
    assert recent[0]["reason_code"] == "B"
