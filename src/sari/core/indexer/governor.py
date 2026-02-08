import os
import time
import logging
from typing import Dict, Any

try:
    import psutil
except ImportError:
    psutil = None

class ResourceGovernor:
    """
    Sari Smart Resource Governor.
    Balances between 'Ultra Turbo' performance and system stability.
    """
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("sari.governor")
        self._last_check_ts = 0
        self._cached_factor = 1.0
        self.check_interval = 2.0
        
        if os.environ.get("SARI_TEST_MODE") == "1":
            self.check_interval = 0.0

    def get_concurrency_factor(self) -> float:
        """
        Returns a multiplier for concurrency.
        1.0 = Default
        2.5 = System Idle (Boost)
        0.3 = System Stressed (Throttling)
        """
        now = time.time()
        if now - self._last_check_ts < self.check_interval:
            return self._cached_factor
            
        if not psutil: return 1.0
        
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            
            # Legacy expected logic for tests
            if cpu > 90 or mem > 90: factor = 0.3
            elif cpu < 20 and mem < 40: factor = 2.5
            else: factor = 1.0
            
            self._cached_factor = factor
            self._last_check_ts = now
            return factor
        except:
            return 1.0