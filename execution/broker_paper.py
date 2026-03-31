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
                 fee: float = 0.001,
                 trail_activate_pct: float = 0.01,
                 trail_pct: float = 0.008):
        self.store = store
        self.clock = clock
        self.balance = initial_balance
        self.slippage = slippage
        self.fee = fee
        self._trail_activate_pct = trail_activate_pct  # FIX 8: configurable
        self._trail_pct = trail_pct                    # FIX 8: configurable
        self._positions: Dict[str, Position] = {}  # keyed by symbol
        self._orders: Dict[str, Order] = {}

        # Load state from DB
        for pos in self.store.get_open_positions():
            self._positions[pos.symbol] = pos

    # --- Balance ---------------------------------------------------------------

    def get_balance(self) -> float:
        return self.balance

    # --- Orders ----------------------------------------------------------------

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

    # --- Position management ---------------------------------------------------

    def _update_position(self, order: Order, signal: Signal):
        symbol = order.symbol
        if signal.reason == Reason.SIGNAL_ENTRY:
            # Opening a new position (long or short)
            pos = Position(
                id=str(uuid.uuid4()),
                symbol=symbol,
                side=signal.side,
                entry_price=order.filled_price,
                amount=order.filled_amount,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                entry_time=order.timestamp,
                status=PositionStatus.OPEN,
                strategy_params=signal.params,
                commission=order.filled_amount * order.filled_price * self.fee
            )
            self._positions[symbol] = pos
            self.store.save_position(pos)

        elif symbol in self._positions:
            # Closing an existing position
            pos = self._positions[symbol]
            pos.status = PositionStatus.CLOSED
            pos.exit_price = order.filled_price
            pos.exit_time = order.timestamp
            pos.exit_reason = signal.reason

            if pos.side == Side.BUY:
                pos.pnl = (pos.exit_price - pos.entry_price) * pos.amount
            else:  # Short position
                pos.pnl = (pos.entry_price - pos.exit_price) * pos.amount

            pos.pnl -= (pos.commission + (order.filled_amount * order.filled_price * self.fee))
            pos.pnl_percent = (pos.pnl / (pos.entry_price * pos.amount)) * 100
            self.store.save_position(pos)
            del self._positions[symbol]

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_open_orders(self) -> List[Order]:
        return []

    def get_open_position(self) -> Optional[Position]:
        """Backward compat: returns first open position."""
        if self._positions:
            return next(iter(self._positions.values()))
        return None

    def get_open_positions(self) -> List[Position]:
        return list(self._positions.values())

    def get_position_for_symbol(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def sync(self):
        pass

    # --- Trailing Stop ---------------------------------------------------------

    def update_trailing_stop(self, symbol: str, candle_high: float,
                              candle_low: float, trail_atr: float) -> None:
        """
        Ratchet the stop-loss in the direction of profit.
        For LONG:  new_sl = candle_high - trail_atr -> move up if > current SL
        For SHORT: new_sl = candle_low  + trail_atr -> move down if < current SL
        Stop only ever moves in the direction of profit -- never against it.
        Activates only after position is at least 1R in profit.
        """
        pos = self._positions.get(symbol)
        if not pos or not pos.stop_loss:
            return

        if pos.side == Side.BUY:
            # Only trail after 1R profit
            r = abs(pos.entry_price - pos.stop_loss)
            if candle_high < pos.entry_price + r:
                return   # Not yet 1R in profit
            new_sl = candle_high - trail_atr
            if new_sl > pos.stop_loss:
                pos.stop_loss = new_sl
                self.store.save_position(pos)

        elif pos.side == Side.SELL:
            r = abs(pos.entry_price - pos.stop_loss)
            if candle_low > pos.entry_price - r:
                return   # Not yet 1R in profit
            new_sl = candle_low + trail_atr
            if new_sl < pos.stop_loss:
                pos.stop_loss = new_sl
                self.store.save_position(pos)

    def update_trailing_stop_pct(self, symbol: str, current_price: float,
                                  activate_pct: float = 0.01,
                                  trail_pct: float = 0.008) -> None:
        """
        FIX 8: Percentage-based trailing stop.
        Activates after position reaches +activate_pct profit (default +1%).
        Trails at trail_pct distance (default 0.8%).

        For BUY:  trail_sl = current_price × (1 - trail_pct), only moves UP
        For SELL: trail_sl = current_price × (1 + trail_pct), only moves DOWN
        """
        pos = self._positions.get(symbol)
        if not pos or not pos.stop_loss:
            return

        if pos.side == Side.BUY:
            profit_pct = (current_price - pos.entry_price) / pos.entry_price
            if profit_pct < activate_pct:
                return  # Not yet +1% in profit
            new_sl = current_price * (1 - trail_pct)
            if new_sl > pos.stop_loss:
                pos.stop_loss = new_sl
                self.store.save_position(pos)

        elif pos.side == Side.SELL:
            profit_pct = (pos.entry_price - current_price) / pos.entry_price
            if profit_pct < activate_pct:
                return  # Not yet +1% in profit
            new_sl = current_price * (1 + trail_pct)
            if new_sl < pos.stop_loss:
                pos.stop_loss = new_sl
                self.store.save_position(pos)

    # --- Trailing Entry (from passivbot) -----------------------------------------

    def create_trailing_entry(self, signal: Signal, size: float,
                               trail_pct: float = 0.003) -> str:
        """
        Instead of entering immediately at market, wait for a pullback.
        (Borrowed from enarjord/passivbot trailing entry technique)

        For BUY: set entry trigger at signal.price * (1 - trail_pct).
                 If price pulls back to trigger, then bounces up, fill the order.
        For SELL: set entry trigger at signal.price * (1 + trail_pct).
                  If price bounces up to trigger, then drops, fill the order.

        Returns a pending entry ID. Call check_trailing_entries() each candle.
        """
        entry_id = str(uuid.uuid4())
        if signal.side == Side.BUY:
            trigger_price = signal.price * (1 - trail_pct)
        else:
            trigger_price = signal.price * (1 + trail_pct)

        if not hasattr(self, '_trailing_entries'):
            self._trailing_entries = {}

        self._trailing_entries[entry_id] = {
            'signal': signal,
            'size': size,
            'trigger_price': trigger_price,
            'best_price': signal.price,  # Track best pullback price seen
            'activated': False,          # True once trigger hit
            'created_at': self.clock.now_ms(),
            'expires_at': self.clock.now_ms() + (3600 * 1000),  # 1 hour expiry
        }
        return entry_id

    def check_trailing_entries(self, candle: Candle) -> Optional[Order]:
        """
        Check pending trailing entries against current candle.

        Logic:
        1. Price must first reach the trigger (pullback zone)
        2. Then price must reverse back toward original direction
        3. Fill when price moves 0.1% past the best pullback price

        This avoids buying at the exact top of a breakout candle.
        Expires after 1 hour if trigger never hit.
        """
        if not hasattr(self, '_trailing_entries') or not self._trailing_entries:
            return None

        now = self.clock.now_ms()
        expired = []
        filled_order = None

        for entry_id, entry in self._trailing_entries.items():
            # Check expiry
            if now > entry['expires_at']:
                expired.append(entry_id)
                continue

            signal = entry['signal']

            if signal.side == Side.BUY:
                # Phase 1: Wait for pullback to trigger
                if not entry['activated']:
                    if candle.low <= entry['trigger_price']:
                        entry['activated'] = True
                        entry['best_price'] = candle.low
                    continue

                # Phase 2: Track lowest price during pullback
                if candle.low < entry['best_price']:
                    entry['best_price'] = candle.low

                # Phase 3: Fill when price bounces 0.1% above best pullback
                fill_threshold = entry['best_price'] * 1.001
                if candle.high >= fill_threshold:
                    fill_signal = Signal(
                        symbol=signal.symbol,
                        side=signal.side,
                        reason=signal.reason,
                        price=fill_threshold,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        params=signal.params
                    )
                    filled_order = self.place_order(fill_signal, entry['size'])
                    expired.append(entry_id)
                    break

            elif signal.side == Side.SELL:
                if not entry['activated']:
                    if candle.high >= entry['trigger_price']:
                        entry['activated'] = True
                        entry['best_price'] = candle.high
                    continue

                if candle.high > entry['best_price']:
                    entry['best_price'] = candle.high

                fill_threshold = entry['best_price'] * 0.999
                if candle.low <= fill_threshold:
                    fill_signal = Signal(
                        symbol=signal.symbol,
                        side=signal.side,
                        reason=signal.reason,
                        price=fill_threshold,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        params=signal.params
                    )
                    filled_order = self.place_order(fill_signal, entry['size'])
                    expired.append(entry_id)
                    break

        for eid in expired:
            self._trailing_entries.pop(eid, None)

        return filled_order

    def get_pending_trailing_entries(self) -> int:
        """Return count of pending trailing entries."""
        if not hasattr(self, '_trailing_entries'):
            return 0
        return len(self._trailing_entries)

    # --- SL/TP simulation ------------------------------------------------------

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

        # Ratchet trailing stop before checking hits (ATR-based)
        atr_val = getattr(candle, 'atr', 0)
        trail_atr = atr_val * 1.5 if atr_val else 0
        if trail_atr > 0:
            self.update_trailing_stop(pos.symbol, candle.high, candle.low, trail_atr)

        # FIX 8: Also apply percentage-based trailing stop (tighter, activates at +1%)
        activate_pct = getattr(self, '_trail_activate_pct', 0.01)
        trail_pct = getattr(self, '_trail_pct', 0.008)
        self.update_trailing_stop_pct(pos.symbol, candle.close, activate_pct, trail_pct)

        if pos.side == Side.BUY:
            # Long: SL hit when low <= stop_loss, TP hit when high >= take_profit
            if pos.stop_loss and candle.low <= pos.stop_loss:
                return Signal(
                    symbol=pos.symbol,
                    side=Side.SELL,
                    reason=Reason.STOP_LOSS,
                    price=pos.stop_loss,
                    stop_loss=0, take_profit=0
                )
            if pos.take_profit and candle.high >= pos.take_profit:
                return Signal(
                    symbol=pos.symbol,
                    side=Side.SELL,
                    reason=Reason.TAKE_PROFIT,
                    price=pos.take_profit,
                    stop_loss=0, take_profit=0
                )
        elif pos.side == Side.SELL:
            # Short: SL hit when high >= stop_loss, TP hit when low <= take_profit
            if pos.stop_loss and candle.high >= pos.stop_loss:
                return Signal(
                    symbol=pos.symbol,
                    side=Side.BUY,
                    reason=Reason.STOP_LOSS,
                    price=pos.stop_loss,
                    stop_loss=0, take_profit=0
                )
            if pos.take_profit and candle.low <= pos.take_profit:
                return Signal(
                    symbol=pos.symbol,
                    side=Side.BUY,
                    reason=Reason.TAKE_PROFIT,
                    price=pos.take_profit,
                    stop_loss=0, take_profit=0
                )

        return None

    def check_sl_tp_for_symbol(self, symbol: str, candle: Candle) -> Optional[Signal]:
        """Check SL/TP for a specific symbol's position."""
        return self.check_sl_tp(candle, symbol=symbol)
