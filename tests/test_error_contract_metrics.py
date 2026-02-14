from sari.core.error_contract_metrics import reset_error_contract_metrics_for_tests, snapshot_error_contract_metrics
from sari.mcp.server_request_dispatch import execute_local_method


def test_unknown_tool_error_metric_increments_for_unstructured_tool_error():
    reset_error_contract_metrics_for_tests()
    _ = execute_local_method(
        method="tools/call",
        params={"name": "x"},
        msg_id=101,
        handle_tools_call=lambda _p: {"isError": True},
        dispatch_methods={},
    )
    snap = snapshot_error_contract_metrics()
    assert snap["unknown_tool_error_count"] >= 1
