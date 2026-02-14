from dataclasses import dataclass, field
from enum import Enum, auto
from datetime import datetime
from typing import Optional, Dict, Any, List

class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"     # Triggers market sell
    TAKE_PROFIT = "TAKE_PROFIT" # Limit sell

class OrderStatus(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

class PositionStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"

class Reason(Enum):
    SIGNAL_ENTRY = "SIGNAL_ENTRY"
    SIGNAL_EXIT = "SIGNAL_EXIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    KILL_SWITCH = "KILL_SWITCH"
    MANUAL = "MANUAL"
    RSI_EXIT = "RSI_EXIT"
    TREND_FLIP = "TREND_FLIP"

@dataclass
class Candle:
    timestamp: int  # Ensure milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp / 1000)

@dataclass
class StrategyParams:
    rsi_period: int
    rsi_entry: float
    rsi_exit: float
    ema_fast: int
    ema_slow: int
    atr_period: int
    sl_mult: float
    tp_mult: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rsi_period": self.rsi_period,
            "rsi_entry": self.rsi_entry,
            "rsi_exit": self.rsi_exit,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "atr_period": self.atr_period,
            "sl_mult": self.sl_mult,
            "tp_mult": self.tp_mult
        }

@dataclass
class Signal:
    symbol: str
    side: Side
    reason: Reason
    price: float
    stop_loss: float
    take_profit: Optional[float]
    strength: float = 1.0
    params: Optional[StrategyParams] = None

@dataclass
class Order:
    id: str  # Exchange ID or internal UUID for paper
    symbol: str
    side: Side
    order_type: OrderType
    amount: float
    price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_amount: float = 0.0
    filled_price: float = 0.0  # Average
    timestamp: int = 0
    client_order_id: Optional[str] = None

@dataclass
class Position:
    id: str
    symbol: str
    side: Side
    entry_price: float
    amount: float
    stop_loss: float
    take_profit: Optional[float]
    entry_time: int
    status: PositionStatus = PositionStatus.OPEN
    exit_price: Optional[float] = None
    exit_time: Optional[int] = None
    exit_reason: Optional[Reason] = None
    pnl: float = 0.0
    pnl_percent: float = 0.0
    commission: float = 0.0
    strategy_params: Optional[StrategyParams] = None  # To track which arm opened it

@dataclass
class Trade:
    id: str
    position_id: str
    symbol: str
    side: Side
    price: float
    amount: float
    commission: float
    timestamp: int
    reason: Reason
