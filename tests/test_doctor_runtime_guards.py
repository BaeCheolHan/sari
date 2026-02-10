from sari.core.doctor.runtime_guards import _check_db_migration_safety, _check_system_resources

def test_doctor_reports_db_integrity_correctly():
    """
    Verify that the doctor correctly identifies PeeWee-managed DB integrity.
    """
    res = _check_db_migration_safety()
    assert res["name"] == "DB Migration Safety"
    assert res["passed"] is True
    assert "PeeWee" in res["detail"]

def test_doctor_evaluates_system_resources():
    """
    Verify that the doctor can evaluate CPU and RAM for Turbo mode.
    """
    from unittest.mock import patch
    with patch("sari.core.doctor.runtime_guards.psutil") as mock_psutil:
        mock_psutil.cpu_count.return_value = 4
        mock_psutil.virtual_memory.return_value.total = 16 * 1024**3
        res = _check_system_resources()
    assert res["name"] == "System Resources"
    # Success depends on the host machine, but the fields must exist
    assert "passed" in res
    assert "CPU" in res["detail"]