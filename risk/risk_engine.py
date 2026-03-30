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
        total_risk = 0.0
        for pos in open_positions:
            sl_dist = abs(pos.entry_price - pos.stop_loss) if pos.stop_loss else 0
            total_risk += sl_dist * pos.amount

        new_risk = self.total_capital * (self.risk_per_trade_percent / 100.0)
        total_risk += new_risk

        max_portfolio = self.total_capital * (self.max_portfolio_risk_percent / 100.0)
        if total_risk > max_portfolio:
            return False, f"Portfolio risk {total_risk:.2f} would exceed {self.max_portfolio_risk_percent}% cap ({max_portfolio:.2f})"

        return True, "OK"

    def get_dynamic_risk_percent(
        self,
        current_balance: float,
        base_balance: float,
        setup_score: float,
        peak_balance: float
    ) -> float:
        """
        Dynamic risk % based on account growth phase and setup quality.

        Growth phases (from INVESTOR_MINDSET.md compounding plan):
          Phase 1: balance < 2.5x base  -> 3.0% base risk
          Phase 2: balance < 5.0x base  -> 3.5% base risk
          Phase 3: balance >= 5.0x base -> 4.0% base risk

        Setup score multiplier:
          score >= 80 -> 1.5x  (high conviction -- size up)
          score >= 65 -> 1.0x  (standard)
          score <  65 -> 0.75x (low conviction -- size down)

        Drawdown protection:
          If drawdown from peak > 20% -> reset to Phase 1 risk (3.0%)

        Hard cap: never exceed 5.0% of current balance on any single trade.
        """
        # Drawdown check
        if peak_balance > 0:
            drawdown = (peak_balance - current_balance) / peak_balance
            if drawdown > 0.20:
                base_risk = 3.0  # Reset to Phase 1
            elif current_balance >= base_balance * 5.0:
                base_risk = 4.0  # Phase 3
            elif current_balance >= base_balance * 2.5:
                base_risk = 3.5  # Phase 2
            else:
                base_risk = 3.0  # Phase 1
        else:
            base_risk = 3.0

        # Score multiplier
        if setup_score >= 80:
            multiplier = 1.5
        elif setup_score >= 65:
            multiplier = 1.0
        else:
            multiplier = 0.75

        risk_pct = base_risk * multiplier
        return min(risk_pct, 5.0)  # Hard cap at 5%

    def get_kelly_risk_percent(
        self,
        win_probability: float,
        avg_win_loss_ratio: float = 2.0,
        fraction: float = 0.25
    ) -> float:
        """
        Kelly Criterion position sizing — mathematically optimal bet size.
        (Borrowed from mfzhang/crypto-trading-bot)

        Full Kelly: f = p - (1-p)/b
          where p = win probability, b = avg_win / avg_loss ratio

        We use fractional Kelly (default 25%) to reduce variance while
        keeping most of the edge. Full Kelly is too aggressive for trading.

        Args:
            win_probability: Model's predicted P(win), 0.0-1.0
            avg_win_loss_ratio: Historical avg win / avg loss (default 2.0 = 2R)
            fraction: Kelly fraction (0.25 = quarter Kelly, conservative)

        Returns:
            Risk percentage (0.0-5.0), capped at 5% hard limit.
        """
        if win_probability <= 0 or avg_win_loss_ratio <= 0:
            return 0.0

        # Kelly formula
        kelly_f = win_probability - (1 - win_probability) / avg_win_loss_ratio

        if kelly_f <= 0:
            return 0.0  # Negative edge — don't bet

        # Fractional Kelly to reduce variance
        risk_pct = kelly_f * fraction * 100.0

        # Floor at 1% so we still take the trade, cap at 5%
        return max(1.0, min(risk_pct, 5.0))

    def calculate_position_size(self, signal: Signal, reserved_capital: float = 0.0,
                                 dynamic_risk_pct: float = None) -> float:
        """
        Calculate position size based on risk percentage and distance to stop loss.
        If dynamic_risk_pct is provided, uses that instead of fixed risk_per_trade_percent.
        """
        if not signal.stop_loss:
            return 0.0

        available_capital = self.total_capital - reserved_capital
        if available_capital <= 0:
            return 0.0

        risk_pct = dynamic_risk_pct if dynamic_risk_pct is not None else self.risk_per_trade_percent
        risk_amount = available_capital * (risk_pct / 100.0)
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

        min_cost = limits.get('cost', {}).get('min') or 0
        min_amount = limits.get('amount', {}).get('min') or 0

        if cost < min_cost:
            return False, f"Cost {cost} < Min {min_cost}"

        if size < min_amount:
            return False, f"Size {size} < Min {min_amount}"

        return True, "OK"

    @staticmethod
    def risk_to_qty(
        capital: float,
        risk_pct: float,
        entry_price: float,
        stop_price: float,
        market_structure: dict = None
    ) -> float:
        """
        Convert a risk percentage into an exchange-valid order quantity.
        Handles lot size precision, step size rounding, and min notional.
        (Borrowed from jesse-ai/jesse's risk_to_qty utility)

        Args:
            capital: Available USDT balance
            risk_pct: Risk as percentage (e.g. 3.0 for 3%)
            entry_price: Expected entry price
            stop_price: Stop-loss price
            market_structure: ccxt market info dict (from exchange.market(symbol))

        Returns:
            Exchange-valid quantity, or 0.0 if order would be below minimums.
        """
        import math

        sl_distance = abs(entry_price - stop_price)
        if sl_distance == 0 or entry_price == 0:
            return 0.0

        risk_amount = capital * (risk_pct / 100.0)
        qty = risk_amount / sl_distance

        # Cap to available capital
        max_qty = capital / entry_price
        qty = min(qty, max_qty)

        if not market_structure:
            return qty

        # Apply exchange precision constraints
        limits = market_structure.get('limits', {})
        precision = market_structure.get('precision', {})

        # Step size / amount precision
        amount_precision = precision.get('amount')
        if amount_precision is not None:
            if isinstance(amount_precision, int):
                # Decimal places (e.g. 3 means 0.001 step)
                factor = 10 ** amount_precision
                qty = math.floor(qty * factor) / factor
            elif isinstance(amount_precision, float) and amount_precision > 0:
                # Step size (e.g. 0.01)
                qty = math.floor(qty / amount_precision) * amount_precision

        # Min amount check
        min_amount = limits.get('amount', {}).get('min')
        if min_amount and qty < min_amount:
            return 0.0

        # Min cost (notional) check
        min_cost = limits.get('cost', {}).get('min')
        if min_cost and (qty * entry_price) < min_cost:
            return 0.0

        # Max amount check
        max_amount = limits.get('amount', {}).get('max')
        if max_amount and qty > max_amount:
            qty = max_amount

        return qty
