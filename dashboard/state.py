"""
Dashboard shared state — thread-safe container for multi-position data.
Updated by the main loop each cycle; read by dashboard routes.
"""
import threading
from typing import Dict, List, Any


class DashboardState:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {
            "positions_summary": [],
            "scan_results": [],
            "open_positions_count": 0,
            "last_cycle": None,
            "total_balance": 0.0,
            "daily_pnl": 0.0,
            "breaker_status": "OK",
            "scanner_enabled": False,
            "max_positions": 1,
        }

    def update(self, updates: Dict[str, Any]):
        with self._lock:
            self._data.update(updates)

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)


# Global singleton
shared_state = DashboardState()
