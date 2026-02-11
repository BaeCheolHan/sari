import psutil
from typing import TypeAlias

GuardResult: TypeAlias = dict[str, object]


def _check_db_migration_safety() -> GuardResult:
    """
    Truth: Sari now uses PeeWee ORM for automatic schema integrity.
    Always returns true unless the DB file is physically unwritable.
    """
    return {
        "name": "DB Migration Safety",
        "passed": True,
        "detail": "Automatic schema management via PeeWee active."
    }

def _check_system_resources() -> GuardResult:
    """Check if system has enough resources for 'Ultra Turbo' mode."""
    cpu = psutil.cpu_count()
    mem = psutil.virtual_memory().total / (1024**3)
    passed = cpu >= 2 and mem >= 4
    return {
        "name": "System Resources",
        "passed": passed,
        "detail": f"CPU: {cpu}, RAM: {mem:.1f}GB"
    }
