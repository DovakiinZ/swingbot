import uuid
from typing import List, Optional, Dict
from core.types import Order, Position, Signal, Side, OrderType, OrderStatus, PositionStatus, Reason, Candle
from core.clock import Clock
from execution.broker_base import Broker
from storage.sqlite_store import SQLiteStore

class PaperBroker(Broker):
    def __init__(self, 
                 store: SQLiteStore, 
                 clock: Clock, 
                 initial_balance: float = 50.0,
                 slippage: float = 0.001,
                 fee: float = 0.001):
        self.store = store
        self.clock = clock
        self.balance = initial_balance
        self.slippage = slippage
        self.fee = fee
        self._position: Optional[Position] = None
        self._orders: Dict[str, Order] = {}
        
        # Load state from DB
        self._position = self.store.get_open_position() # Not implemented in store yet, need to fix store

    def get_balance(self) -> float:
        # Simple paper balance: Cash + Unrealized PnL? 
        # Usually just Cash for purchasing power.
        return self.balance

    def place_order(self, signal: Signal, size: float) -> Optional[Order]:
        # Simulate immediate fill at next candle OPEN or current CLOSE? 
        # Requirement: "Simulate fills at next candle open or current close"
        # We'll assume current signal price (close of generating candle) for simplicity, 
        # or we wait for next tick.
        # Let's fill immediately at signal.price +/- slippage for paper.
        
        price = signal.price
        if signal.side == Side.BUY:
            fill_price = price * (1 + self.slippage)
        else:
            fill_price = price * (1 - self.slippage)
            
        commission = size * fill_price * self.fee
        
        order_id = str(uuid.uuid4())
        order = Order(
            id=order_id,
            symbol=signal.symbol,
            side=signal.side,
            order_type=OrderType.MARKET,
            amount=size,
            price=signal.price,
            status=OrderStatus.FILLED,
            filled_amount=size,
            filled_price=fill_price,
            timestamp=self.clock.now_ms()
        )
        
        self.store.save_order(order)
        self._update_position(order, signal)
        
        # Deduct fees from balance
        self.balance -= commission
        # If BUY, we spent cash. If SELL, we got cash.
        if signal.side == Side.BUY:
            self.balance -= (size * fill_price)
        else:
            self.balance += (size * fill_price)
            
        return order

    def _update_position(self, order: Order, signal: Signal):
        if order.side == Side.BUY:
            # Open Position
            # Need to handle avg price if adding to position (pyramiding), 
            # but we have max 1 position constraint.
            self._position = Position(
                id=str(uuid.uuid4()),
                symbol=order.symbol,
                side=Side.BUY,
                entry_price=order.filled_price,
                amount=order.filled_amount,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                entry_time=order.timestamp,
                status=PositionStatus.OPEN,
                strategy_params=signal.params,
                commission=order.filled_amount * order.filled_price * self.fee
            )
            self.store.save_position(self._position)
            
        elif order.side == Side.SELL and self._position:
            # Close Position
            pos = self._position
            pos.status = PositionStatus.CLOSED
            pos.exit_price = order.filled_price
            pos.exit_time = order.timestamp
            pos.exit_reason = signal.reason
            
            # PnL Calculation
            # (Exit - Entry) * Amount
            pos.pnl = (pos.exit_price - pos.entry_price) * pos.amount
            pos.pnl -= (pos.commission + (order.filled_amount * order.filled_price * self.fee)) # Total fees
            pos.pnl_percent = (pos.pnl / (pos.entry_price * pos.amount)) * 100
            
            self.store.save_position(pos)
            self._position = None

    def cancel_order(self, order_id: str) -> bool:
        return True # Immediate fills in paper, nothing to cancel

    def get_open_orders(self) -> List[Order]:
        return []

    def get_open_position(self) -> Optional[Position]:
        return self._position

    def sync(self):
        pass

    def check_sl_tp(self, candle: Candle) -> Optional[Signal]:
        """
        Paper sim specific: Called each tick/candle to check if SL/TP hit.
        """
        if not self._position:
            return None
            
        pos = self._position
        
        # Check SL
        if pos.stop_loss and candle.low <= pos.stop_loss:
             return Signal(
                symbol=pos.symbol,
                side=Side.SELL,
                reason=Reason.STOP_LOSS,
                price=pos.stop_loss, # Fill at SL price (simulated)
                stop_loss=0, take_profit=0
            )
            
        # Check TP
        if pos.take_profit and candle.high >= pos.take_profit:
            return Signal(
                symbol=pos.symbol,
                side=Side.SELL,
                reason=Reason.TAKE_PROFIT,
                price=pos.take_profit,
                stop_loss=0, take_profit=0
            )
            
        return None
