import pytest
import psutil
from unittest.mock import patch
from sari.core.indexer.governor import ResourceGovernor

def test_resource_governor_real_world_factors():
    """
    Verify that the governor correctly identifies system states.
    """
    governor = ResourceGovernor()
    governor.check_interval = 0 # Force immediate re-check
    
    # 1. Idle State -> Boost
    with patch("psutil.cpu_percent", return_value=5.0), \
         patch("psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.percent = 10.0
        assert governor.get_concurrency_factor() == 2.5
        
    # 2. Stressed State -> Throttle
    with patch("psutil.cpu_percent", return_value=95.0), \
         patch("psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.percent = 95.0
        assert governor.get_concurrency_factor() == 0.3