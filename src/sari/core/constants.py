"""
Centralized constants for Sari project.

This module contains all magic numbers and configuration defaults
to improve code readability and maintainability.
"""

# ============================================================================
# Daemon Configuration
# ============================================================================

DEFAULT_DAEMON_HOST = "127.0.0.1"
"""Default host for Sari daemon MCP server."""

DEFAULT_DAEMON_PORT = 47779
"""Default port for Sari daemon MCP server."""

DEFAULT_HTTP_HOST = "127.0.0.1"
"""Default host for Sari HTTP server."""

DEFAULT_HTTP_PORT = 47777
"""Default port for Sari HTTP server."""


# ============================================================================
# Watcher Configuration
# ============================================================================

WATCHER_DEBOUNCE_MIN_SECONDS = 0.1
"""Minimum debounce interval for file system events (100ms for LLM freshness)."""

WATCHER_DEBOUNCE_MAX_SECONDS = 1.0
"""Maximum debounce interval for file system events."""

WATCHER_TARGET_RPS = 50.0
"""Target events per second for adaptive debouncing."""

WATCHER_RATE_WINDOW_SECONDS = 1.0
"""Time window for rate calculation (seconds)."""

WATCHER_BUCKET_CAPACITY = 100.0
"""Token bucket capacity for burst control."""

WATCHER_BUCKET_RATE = 50.0
"""Token refill rate per second."""

WATCHER_BUCKET_FLUSH_SECONDS = 0.1
"""Interval for flushing pending events from token bucket."""

WATCHER_GIT_DEBOUNCE_SECONDS = 3.0
"""Debounce interval for git checkout events."""

WATCHER_MONITOR_INTERVAL_SECONDS = 10.0
"""Interval for observer health check monitoring."""

WATCHER_OBSERVER_TIMEOUT_SECONDS = 0.1
"""Timeout for watchdog observer (low latency)."""

WATCHER_EVENT_QUEUE_MAXLEN = 200
"""Maximum length of event times deque for rate calculation."""

WATCHER_RESTART_MAX_RETRIES = 3
"""Maximum retries for observer restart."""

WATCHER_RESTART_RETRY_DELAY_SECONDS = 1.0
"""Delay between restart attempts."""

WATCHER_OBSERVER_JOIN_TIMEOUT_SECONDS = 5.0
"""Timeout for waiting observer to stop during restart."""


# ============================================================================
# Timeouts
# ============================================================================

DAEMON_IDENTIFY_TIMEOUT_SECONDS = 1.0
"""Timeout for daemon identification probe."""

HTTP_CHECK_TIMEOUT_SECONDS = 0.4
"""Timeout for HTTP server health check."""

DAEMON_PROBE_TIMEOUT_SECONDS = 0.3
"""Timeout for daemon probe (quick check)."""


# ============================================================================
# Environment Variable Names
# ============================================================================

ENV_SARI_GIT_CHECKOUT_DEBOUNCE = "SARI_GIT_CHECKOUT_DEBOUNCE"
"""Environment variable for git checkout debounce override."""

ENV_SARI_WATCHER_MONITOR_SECONDS = "SARI_WATCHER_MONITOR_SECONDS"
"""Environment variable for watcher monitor interval override."""
