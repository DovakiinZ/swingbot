from typing import Tuple
from core.types import Signal, Position
from data.market import MarketData

class RiskEngine:
    def __init__(self, 
                 total_capital: float, 
                 risk_per_trade_percent: float = 1.0, 
                 max_open_positions: int = 1):
        self.total_capital = total_capital
        self.risk_per_trade_percent = risk_per_trade_percent
        self.max_open_positions = max_open_positions

    def can_open_new_position(self, current_positions: int) -> bool:
        return current_positions < self.max_open_positions

    def calculate_position_size(self, signal: Signal) -> float:
        """
        Calculate position size based on risk percentage and distance to stop loss.
        Risk Amount = Capital * (Risk% / 100)
        Size = Risk Amount / (Entry - SL)
        """
        if not signal.stop_loss:
            return 0.0

        risk_amount = self.total_capital * (self.risk_per_trade_percent / 100.0)
        price = signal.price
        
        # Distance per unit
        sl_distance = abs(price - signal.stop_loss)
        
        if sl_distance == 0:
            return 0.0
            
        position_size = risk_amount / sl_distance
        
        # Cap size to buying power usually, but simplified here:
        # If size * price > capital, we can't afford it (or need leverage, which is OFF)
        if position_size * price > self.total_capital:
            position_size = self.total_capital / price
            
        return position_size

    def check_min_notional(self, size: float, price: float, market_structure: dict) -> Tuple[bool, str]:
        """
        Verify if size meets exchange requirements (min notional, min quantity).
        """
        if not market_structure:
            return True, "" # Skip if no data
            
        limits = market_structure.get('limits', {})
        cost = size * price
        
        min_cost = limits.get('cost', {}).get('min', 0)
        min_amount = limits.get('amount', {}).get('min', 0)
        
        if cost < min_cost:
            return False, f"Cost {cost} < Min {min_cost}"
            
        if size < min_amount:
            return False, f"Size {size} < Min {min_amount}"
            
        return True, "OK"
