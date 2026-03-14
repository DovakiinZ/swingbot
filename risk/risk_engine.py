from typing import Tuple, List
from core.types import Signal, Position

class RiskEngine:
    def __init__(self,
                 total_capital: float,
                 risk_per_trade_percent: float = 1.0,
                 max_open_positions: int = 1,
                 max_portfolio_risk_percent: float = 5.0,
                 max_single_position_percent: float = 30.0):
        self.total_capital = total_capital
        self.risk_per_trade_percent = risk_per_trade_percent
        self.max_open_positions = min(max_open_positions, 5)  # Hard cap at 5
        self.max_portfolio_risk_percent = max_portfolio_risk_percent
        self.max_single_position_percent = max_single_position_percent

    def can_open_new_position(self, current_positions: int) -> bool:
        return current_positions < self.max_open_positions

    def can_open_position_for_symbol(self, symbol: str, open_positions: List[Position],
                                      size: float, price: float) -> Tuple[bool, str]:
        """
        Portfolio-level risk check for opening a new position.
        Checks:
        - Max position count not exceeded
        - No duplicate symbol
        - Total portfolio risk under max_portfolio_risk_percent
        - Single position under max_single_position_percent
        """
        # 1. Position count
        if len(open_positions) >= self.max_open_positions:
            return False, f"Max positions ({self.max_open_positions}) reached"

        # 2. No duplicate symbol
        for pos in open_positions:
            if pos.symbol == symbol:
                return False, f"Already have open position for {symbol}"

        # 3. Single position size cap
        position_value = size * price
        max_single = self.total_capital * (self.max_single_position_percent / 100.0)
        if position_value > max_single:
            return False, f"Position value {position_value:.2f} > {self.max_single_position_percent}% of capital ({max_single:.2f})"

        # 4. Total portfolio risk cap
        total_allocated = sum(p.entry_price * p.amount for p in open_positions)
        total_allocated += position_value
        max_portfolio = self.total_capital * (self.max_portfolio_risk_percent / 100.0)

        # Check total risk (sum of distances to SL as % of capital)
        total_risk = 0.0
        for pos in open_positions:
            sl_dist = abs(pos.entry_price - pos.stop_loss) if pos.stop_loss else 0
            total_risk += sl_dist * pos.amount

        # Add new position's risk
        # We don't have SL here directly, but we can estimate from risk_per_trade
        new_risk = self.total_capital * (self.risk_per_trade_percent / 100.0)
        total_risk += new_risk

        if total_risk > max_portfolio:
            return False, f"Portfolio risk {total_risk:.2f} would exceed {self.max_portfolio_risk_percent}% cap ({max_portfolio:.2f})"

        return True, "OK"

    def calculate_position_size(self, signal: Signal, reserved_capital: float = 0.0) -> float:
        """
        Calculate position size based on risk percentage and distance to stop loss.
        reserved_capital: capital already allocated to other open positions.
        """
        if not signal.stop_loss:
            return 0.0

        available_capital = self.total_capital - reserved_capital
        if available_capital <= 0:
            return 0.0

        risk_amount = available_capital * (self.risk_per_trade_percent / 100.0)
        price = signal.price

        sl_distance = abs(price - signal.stop_loss)

        if sl_distance == 0:
            return 0.0

        position_size = risk_amount / sl_distance

        # Cap to available capital
        if position_size * price > available_capital:
            position_size = available_capital / price

        # Cap to single position limit
        max_single = self.total_capital * (self.max_single_position_percent / 100.0)
        if position_size * price > max_single:
            position_size = max_single / price

        return position_size

    def check_min_notional(self, size: float, price: float, market_structure: dict) -> Tuple[bool, str]:
        if not market_structure:
            return True, ""

        limits = market_structure.get('limits', {})
        cost = size * price

        min_cost = limits.get('cost', {}).get('min', 0)
        min_amount = limits.get('amount', {}).get('min', 0)

        if cost < min_cost:
            return False, f"Cost {cost} < Min {min_cost}"

        if size < min_amount:
            return False, f"Size {size} < Min {min_amount}"

        return True, "OK"
