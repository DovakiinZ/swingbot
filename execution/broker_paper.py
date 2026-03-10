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
        # Multi-position support: keyed by symbol
        self._positions: Dict[str, Position] = {}
        self._orders: Dict[str, Order] = {}

        # Restore any open positions from DB on startup
        for pos in self.store.get_open_positions():
            self._positions[pos.symbol] = pos

    # ─── Balance ──────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        """Cash available for new purchases."""
        return self.balance

    # ─── Orders ───────────────────────────────────────────────────────────────

    def place_order(self, signal: Signal, size: float) -> Optional[Order]:
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

        # Cash accounting
        self.balance -= commission
        if signal.side == Side.BUY:
            self.balance -= (size * fill_price)
        else:
            self.balance += (size * fill_price)

        return order

    # ─── Position management ──────────────────────────────────────────────────

    def _update_position(self, order: Order, signal: Signal):
        if order.side == Side.BUY:
            pos = Position(
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
            self._positions[order.symbol] = pos
            self.store.save_position(pos)

        elif order.side == Side.SELL:
            pos = self._positions.get(order.symbol)
            if not pos:
                return
            pos.status = PositionStatus.CLOSED
            pos.exit_price = order.filled_price
            pos.exit_time = order.timestamp
            pos.exit_reason = signal.reason
            pos.pnl = (pos.exit_price - pos.entry_price) * pos.amount
            pos.pnl -= (pos.commission + (order.filled_amount * order.filled_price * self.fee))
            pos.pnl_percent = (pos.pnl / (pos.entry_price * pos.amount)) * 100
            self.store.save_position(pos)
            del self._positions[order.symbol]

    def cancel_order(self, order_id: str) -> bool:
        return True  # Immediate fills in paper mode

    def get_open_orders(self) -> List[Order]:
        return []

    def get_open_position(self) -> Optional[Position]:
        """Backward-compat: return any single open position, or None."""
        if self._positions:
            return next(iter(self._positions.values()))
        return None

    def get_open_positions(self) -> List[Position]:
        return list(self._positions.values())

    def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def sync(self):
        pass

    # ─── SL/TP simulation ─────────────────────────────────────────────────────

    def check_sl_tp(self, candle: Candle, symbol: str = None) -> Optional[Signal]:
        """
        Check stop-loss / take-profit for a specific symbol (or the only open
        position when symbol is omitted for backward compatibility).
        """
        if symbol:
            pos = self._positions.get(symbol)
        elif self._positions:
            pos = next(iter(self._positions.values()))
        else:
            return None

        if not pos:
            return None

        # Stop-loss
        if pos.stop_loss and candle.low <= pos.stop_loss:
            return Signal(
                symbol=pos.symbol,
                side=Side.SELL,
                reason=Reason.STOP_LOSS,
                price=pos.stop_loss,
                stop_loss=0, take_profit=0
            )

        # Take-profit
        if pos.take_profit and candle.high >= pos.take_profit:
            return Signal(
                symbol=pos.symbol,
                side=Side.SELL,
                reason=Reason.TAKE_PROFIT,
                price=pos.take_profit,
                stop_loss=0, take_profit=0
            )

        return None
