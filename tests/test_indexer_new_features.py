import pytest
import time
import psutil
from unittest.mock import patch
from sari.core.indexer.governor import ResourceGovernor

def test_resource_governor_real_world_factors():
    """
    Verify that the modernized EMA-based governor correctly adjusts factors.
    """
    governor = ResourceGovernor()
    governor.check_interval = 0 # Force immediate re-check

    # 1. Simulate sustained Idle State -> Should Boost (> 1.0)
    with patch("psutil.cpu_percent", return_value=5.0), \
         patch("psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.percent = 10.0
        
        # EMA needs multiple pulses to adjust
        factor = 1.0
        for _ in range(15): # Enough pulses to move from 50% to ~5%
            factor = governor.get_concurrency_factor()
        
        assert factor > 1.5, f"Should boost on idle, got {factor}"
        assert factor <= 3.0

    # 2. Simulate sustained Stress State -> Should Throttle (< 1.0)
    with patch("psutil.cpu_percent", return_value=95.0), \
         patch("psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.percent = 95.0
        
        factor = 1.0
        for _ in range(15):
            factor = governor.get_concurrency_factor()
            
        assert factor < 0.5, f"Should throttle on stress, got {factor}"
        assert factor >= 0.1
