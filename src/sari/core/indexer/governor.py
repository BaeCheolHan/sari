import os
import time
import logging

try:
    import psutil
except ImportError:
    psutil = None

class ResourceGovernor:

    """

    Sari Smart Resource Governor (Modernized).

    Uses Exponential Moving Average (EMA) to smooth out system load spikes

    and provides a continuous concurrency factor to prevent thrashing.

    """

    def __init__(self, logger=None):

        self.logger = logger or logging.getLogger("sari.governor")

        self._last_check_ts = 0

        self._cached_factor = 1.0

        self.check_interval = 2.0

        

        # EMA states (0-100 scale)

        self._ema_cpu = 50.0

        self._ema_mem = 50.0

        self._alpha = 0.3 # Smoothing factor (0 < alpha < 1)

        

        if os.environ.get("SARI_TEST_MODE") == "1":

            self.check_interval = 0.0



    def get_concurrency_factor(self) -> float:

        """

        Calculates a smoothed multiplier for indexing concurrency.

        Returns a value between 0.1 (Heavy Load) and 3.0 (Idle Boost).

        """

        now = time.time()

        if now - self._last_check_ts < self.check_interval:

            return self._cached_factor

            

        if not psutil:

            return 1.0

        

        try:

            # 1. Capture current raw metrics

            curr_cpu = psutil.cpu_percent(interval=None)

            curr_mem = psutil.virtual_memory().percent

            

            # 2. Update EMA (Smooths out short-lived spikes)

            self._ema_cpu = (self._alpha * curr_cpu) + ((1 - self._alpha) * self._ema_cpu)

            self._ema_mem = (self._alpha * curr_mem) + ((1 - self._alpha) * self._ema_mem)

            

            # 3. Determine worst-case stress level

            stress = max(self._ema_cpu, self._ema_mem)

            

            # 4. Continuous Scaling Logic

            if stress > 95:

                factor = 0.1  # Emergency Throttling

            elif stress > 80:

                # Gradual decrease from 1.0 to 0.3

                factor = 1.0 - ((stress - 80) / 15 * 0.7)

            elif stress < 30:

                # Boost when idle (up to 3.0)

                factor = 1.0 + ((30 - stress) / 30 * 2.0)

            else:

                factor = 1.0 # Normal operation zone

            

            # Ensure safety bounds

            factor = max(0.1, min(factor, 3.0))

            

            # 5. Logging significant changes

            if abs(self._cached_factor - factor) > 0.5:

                self.logger.info(f"Resource adjustment: factor={factor:.2f} (CPU EMA:{self._ema_cpu:.1f}%, MEM EMA:{self._ema_mem:.1f}%)")

            

            self._cached_factor = factor

            self._last_check_ts = now

            return factor

        except Exception as e:

            self.logger.debug(f"Governor update failed: {e}")

            return 1.0
