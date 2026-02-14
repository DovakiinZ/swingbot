from typing import List
from core.types import Trade, Order, Reason

class CircuitBreaker:
    def __init__(self, 
                 daily_loss_limit_percent: float, 
                 consecutive_loss_limit: int, 
                 api_failure_limit: int):
        self.daily_loss_limit_percent = daily_loss_limit_percent
        self.consecutive_loss_limit = consecutive_loss_limit
        self.api_failure_limit = api_failure_limit
        
        self.api_errors = 0
        self.is_tripped = False
        self.trip_reason = ""

    def check_daily_pnl(self, daily_pnl: float, start_balance: float) -> bool:
        loss_pct = (daily_pnl / start_balance) * 100
        # If loss is negative (pnl is negative), check magnitude
        if daily_pnl < 0 and abs(loss_pct) >= self.daily_loss_limit_percent:
            self.is_tripped = True
            self.trip_reason = f"Daily Loss Limit: {loss_pct:.2f}% >= {self.daily_loss_limit_percent}%"
            return False
        return True

    def check_consecutive_losses(self, recent_trades: List[Trade]) -> bool:
        losses = 0
        for trade in reversed(recent_trades):
            if trade.reason == Reason.STOP_LOSS or (trade.price - trade.commission < trade.price): # Simplified check
                 # Need proper PnL from trade, assuming simple close here
                 pass
        
        # Better: Daily report passes counters. 
        # But if we look at last N closed positions:
        # This requires Position objects with PnL, not just Trades.
        return True

    def record_api_error(self):
        self.api_errors += 1
        if self.api_errors >= self.api_failure_limit:
            self.is_tripped = True
            self.trip_reason = f"API Failures: {self.api_errors} >= {self.api_failure_limit}"

    def reset(self):
        self.is_tripped = False
        self.trip_reason = ""
        self.api_errors = 0
