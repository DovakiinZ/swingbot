import time
from datetime import datetime, timezone

class Clock:
    """
    Handles time operations, ensuring consistency between
    Paper mode (simulation time) and Live mode (real time).
    """
    def __init__(self, mode: str = "live"):
        self.mode = mode
        self.simulated_time_ms: int = 0

    def now_ms(self) -> int:
        if self.mode == "paper":
            if self.simulated_time_ms == 0:
                raise ValueError("Simulated time not set for paper trading")
            return self.simulated_time_ms
        return int(time.time() * 1000)

    def now_dt(self) -> datetime:
        return datetime.fromtimestamp(self.now_ms() / 1000, tz=timezone.utc)

    def set_time(self, timestamp_ms: int):
        """Only used in paper/backtest mode"""
        self.simulated_time_ms = timestamp_ms

    @staticmethod
    def timestamp_to_dt(ts_ms: int) -> datetime:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
