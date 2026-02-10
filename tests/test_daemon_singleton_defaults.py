from sari.core.constants import DEFAULT_DAEMON_PORT
from sari.core import daemon_resolver
import sari.mcp.daemon as daemon_mod
import sari.mcp.cli.daemon as cli_daemon


def test_daemon_defaults_are_consistent():
    assert daemon_resolver.DEFAULT_PORT == DEFAULT_DAEMON_PORT
    assert daemon_mod.DEFAULT_PORT == DEFAULT_DAEMON_PORT
    assert cli_daemon.DEFAULT_PORT == DEFAULT_DAEMON_PORT
