"""
Binance live broker.

Three hard-won live-trading guarantees this broker enforces (the things that
make live diverge from paper and quietly bleed money):

1. PRICE PROTECTION (no naked market orders)
   Market orders accept *any* fill price -- on a thin book a single order can
   walk the book and fill far from the quote. Every order here is a LIMIT order
   with an Immediate-or-Cancel (IOC) time-in-force and a small marketable
   offset: it crosses the spread to fill now, but the limit price is a hard
   ceiling (buys) / floor (sells) on how bad the fill can be. Anything that
   cannot fill within the offset is cancelled instead of slipping.

2. REAL FEES (no assumed paper fee)
   Position commissions and realized P&L are computed from the ACTUAL fees the
   Binance API reports on each fill (converted to USDT when charged in the base
   asset), never from a static 0.1% assumption.

3. HARD STOP-LOSS ON THE EXCHANGE
   The moment an entry fills, a real STOP_LOSS_LIMIT order is placed on Binance
   for the filled size. The stop lives on the exchange, so it protects the
   position even if the bot process dies, the host loses network, or an API
   call hangs between cycles. Soft (in-Python) stops do none of that.
"""
from typing import List, Optional, Dict
import uuid
import time
import logging

from core.types import (
    Order, Position, Signal, Side, OrderType, OrderStatus,
    PositionStatus, Reason,
)
from data.market import MarketData
from execution.broker_base import Broker
from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class BinanceBroker(Broker):
    def __init__(self,
                 store: SQLiteStore,
                 market: MarketData,
                 limit_offset_pct: float = 0.0015,
                 use_hard_stops: bool = True):
        """
        Args:
            limit_offset_pct: How far past the quote the IOC limit is placed so
                it crosses the spread and fills, while capping worst-case
                slippage. 0.0015 = 0.15%.
            use_hard_stops: Place a real stop-loss order on the exchange right
                after an entry fills.
        """
        self.store = store
        self.market = market
        self.exchange = market.exchange
        self.limit_offset_pct = float(limit_offset_pct)
        self.use_hard_stops = bool(use_hard_stops)
        # symbol -> exchange order id of the live hard stop protecting it.
        self._stop_orders: Dict[str, str] = {}

    # --- Balance ---------------------------------------------------------------

    def get_balance(self) -> float:
        try:
            balance = self.exchange.fetch_balance()
            return float(balance['USDT']['total'])
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return 0.0

    def get_detailed_balance(self) -> Dict[str, float]:
        try:
            bal = self.exchange.fetch_balance()
            return {
                'USDT_free': float(bal.get('USDT', {}).get('free', 0.0)),
                'USDT_total': float(bal.get('USDT', {}).get('total', 0.0)),
                'BTC_total': float(bal.get('BTC', {}).get('total', 0.0)),
            }
        except Exception as e:
            logger.error(f"Error fetching detailed balance: {e}")
            return {'USDT_free': 0.0, 'USDT_total': 0.0, 'BTC_total': 0.0}

    # --- Order placement -------------------------------------------------------

    def place_order(self, signal: Signal, size: float) -> Optional[Order]:
        """Route to open/close based on the signal reason."""
        try:
            if signal.reason == Reason.SIGNAL_ENTRY:
                return self._open_position(signal, size)
            return self._close_position(signal, size)
        except Exception as e:
            logger.error(f"Order placement failed for {signal.symbol}: {e}", exc_info=True)
            return None

    def _open_position(self, signal: Signal, size: float) -> Optional[Order]:
        symbol = signal.symbol
        side = 'buy' if signal.side == Side.BUY else 'sell'

        amount = self._amount_to_precision(symbol, size)
        if amount <= 0:
            logger.warning(f"[Binance] {symbol}: entry size rounds to 0 -- skipped.")
            return None

        limit_price = self._marketable_limit_price(symbol, signal.side, signal.price)

        order_resp = self.exchange.create_order(
            symbol=symbol,
            type='limit',
            side=side,
            amount=amount,
            price=limit_price,
            params={'timeInForce': 'IOC'},   # fill now or cancel -- never rests
        )

        filled_qty = float(order_resp.get('filled') or 0.0)
        avg_price = float(order_resp.get('average') or 0.0)

        if filled_qty <= 0 or avg_price <= 0:
            logger.warning(
                f"[Binance] {symbol}: IOC entry did not fill at limit "
                f"{limit_price} (price moved away). No position opened."
            )
            return None

        fee_usdt = self._extract_fee_usdt(order_resp, symbol, avg_price)

        order = Order(
            id=str(order_resp['id']),
            symbol=symbol,
            side=signal.side,
            order_type=OrderType.LIMIT,
            amount=amount,
            price=signal.price,
            status=OrderStatus.FILLED,
            filled_amount=filled_qty,
            filled_price=avg_price,
            timestamp=int(order_resp.get('timestamp') or int(time.time() * 1000)),
            client_order_id=order_resp.get('clientOrderId'),
        )
        self.store.save_order(order)

        pos = Position(
            id=str(uuid.uuid4()),
            symbol=symbol,
            side=signal.side,
            entry_price=avg_price,
            amount=filled_qty,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            entry_time=order.timestamp,
            status=PositionStatus.OPEN,
            commission=fee_usdt,                 # REAL entry fee, not assumed
            strategy_params=signal.params,
        )
        self.store.save_position(pos)

        logger.warning(
            f"[Binance] OPEN {side.upper()} {filled_qty} {symbol} @ {avg_price:.6f} "
            f"| fee=${fee_usdt:.4f} | limit={limit_price}"
        )

        # Hard stop-loss on the exchange -- survives crashes / API outages.
        if self.use_hard_stops and signal.stop_loss:
            self._place_hard_stop(pos)

        return order

    def _close_position(self, signal: Signal, size: float) -> Optional[Order]:
        symbol = signal.symbol
        pos = self.store.get_open_position_for_symbol(symbol)
        if pos is None:
            logger.warning(f"[Binance] Close requested for {symbol} but no open position found.")
            return None

        side = 'buy' if signal.side == Side.BUY else 'sell'
        amount = self._amount_to_precision(symbol, min(size, pos.amount))
        if amount <= 0:
            logger.warning(f"[Binance] {symbol}: close size rounds to 0 -- skipped.")
            return None

        limit_price = self._marketable_limit_price(symbol, signal.side, signal.price)

        try:
            order_resp = self.exchange.create_order(
                symbol=symbol,
                type='limit',
                side=side,
                amount=amount,
                price=limit_price,
                params={'timeInForce': 'IOC'},
            )
        except Exception as e:
            logger.error(f"[Binance] Close order failed for {symbol}: {e}")
            return None

        filled_qty = float(order_resp.get('filled') or 0.0)
        avg_price = float(order_resp.get('average') or 0.0)

        if filled_qty <= 0 or avg_price <= 0:
            # Exit didn't fill. Leave the hard stop in place (we have NOT
            # cancelled it) so the position stays protected; retry next cycle.
            logger.warning(
                f"[Binance] {symbol}: IOC exit did not fill at {limit_price}. "
                f"Hard stop remains active; will retry."
            )
            return None

        # Exit filled -> the hard stop is now redundant; cancel it so it can't
        # later sell coins we no longer hold.
        self._cancel_hard_stop(symbol)

        fee_usdt = self._extract_fee_usdt(order_resp, symbol, avg_price)
        self._finalize_close(pos, exit_price=avg_price, exit_fee=fee_usdt,
                             reason=signal.reason)

        order = Order(
            id=str(order_resp['id']),
            symbol=symbol,
            side=signal.side,
            order_type=OrderType.LIMIT,
            amount=amount,
            price=signal.price,
            status=OrderStatus.FILLED,
            filled_amount=filled_qty,
            filled_price=avg_price,
            timestamp=int(order_resp.get('timestamp') or int(time.time() * 1000)),
            client_order_id=order_resp.get('clientOrderId'),
        )
        self.store.save_order(order)
        return order

    # --- Hard stop-loss management --------------------------------------------

    def _place_hard_stop(self, pos: Position) -> None:
        """
        Place a real STOP_LOSS_LIMIT order on Binance for an open position.
        Spot can only sell what it holds, so hard stops are placed for LONG
        positions; shorts (futures) would need the linear account and are
        skipped here with a warning.
        """
        if pos.side != Side.BUY:
            logger.warning(
                f"[Binance] {pos.symbol}: hard stop skipped (spot cannot short). "
                f"Position relies on managed exits."
            )
            return
        try:
            stop_price = self._price_to_precision(pos.symbol, pos.stop_loss)
            # Limit a touch below the trigger so the protective sell still fills
            # in a fast drop (never wider than the configured offset).
            limit_price = self._price_to_precision(
                pos.symbol, pos.stop_loss * (1 - self.limit_offset_pct)
            )
            amount = self._amount_to_precision(pos.symbol, pos.amount)
            stop_resp = self.exchange.create_order(
                symbol=pos.symbol,
                type='STOP_LOSS_LIMIT',
                side='sell',
                amount=amount,
                price=limit_price,
                params={'stopPrice': stop_price, 'timeInForce': 'GTC'},
            )
            self._stop_orders[pos.symbol] = str(stop_resp['id'])
            logger.warning(
                f"[Binance] HARD STOP set {pos.symbol} sell {amount} "
                f"trigger={stop_price} limit={limit_price} id={stop_resp['id']}"
            )
        except Exception as e:
            logger.error(
                f"[Binance] FAILED to place hard stop for {pos.symbol}: {e}. "
                f"Position is unprotected on the exchange -- consider closing."
            )

    def _cancel_hard_stop(self, symbol: str) -> None:
        stop_id = self._stop_orders.pop(symbol, None)
        if not stop_id:
            return
        try:
            self.exchange.cancel_order(stop_id, symbol)
            logger.info(f"[Binance] Cancelled hard stop {stop_id} for {symbol}.")
        except Exception as e:
            # Already gone (filled/cancelled) is fine.
            logger.info(f"[Binance] Hard stop {stop_id} for {symbol} not cancellable: {e}")

    # --- Reconciliation --------------------------------------------------------

    def sync(self):
        """
        Detect hard stops that fired on the exchange between cycles and close
        those positions in the store with their real fill price and fee.
        """
        for symbol, stop_id in list(self._stop_orders.items()):
            try:
                o = self.exchange.fetch_order(stop_id, symbol)
            except Exception as e:
                logger.debug(f"[Binance] Could not fetch stop {stop_id} for {symbol}: {e}")
                continue

            status = (o.get('status') or '').lower()
            if status in ('closed', 'filled'):
                pos = self.store.get_open_position_for_symbol(symbol)
                if pos:
                    fill_price = float(o.get('average') or o.get('price') or pos.stop_loss)
                    fee_usdt = self._extract_fee_usdt(o, symbol, fill_price)
                    self._finalize_close(pos, exit_price=fill_price,
                                         exit_fee=fee_usdt, reason=Reason.STOP_LOSS)
                    logger.warning(
                        f"[Binance] Hard stop FILLED for {symbol} @ {fill_price:.6f} "
                        f"-- position closed by exchange."
                    )
                self._stop_orders.pop(symbol, None)
            elif status in ('canceled', 'cancelled', 'expired', 'rejected'):
                # Stop is gone but position may still be open & now unprotected.
                pos = self.store.get_open_position_for_symbol(symbol)
                self._stop_orders.pop(symbol, None)
                if pos and self.use_hard_stops:
                    logger.warning(
                        f"[Binance] Hard stop for {symbol} is {status}; re-placing."
                    )
                    self._place_hard_stop(pos)

    # --- Position close accounting ---------------------------------------------

    def _finalize_close(self, pos: Position, exit_price: float,
                        exit_fee: float, reason: Reason) -> None:
        """Close a position in the store with P&L net of REAL fees."""
        pos.status = PositionStatus.CLOSED
        pos.exit_price = exit_price
        pos.exit_time = int(time.time() * 1000)
        pos.exit_reason = reason

        if pos.side == Side.BUY:
            gross = (exit_price - pos.entry_price) * pos.amount
        else:
            gross = (pos.entry_price - exit_price) * pos.amount

        # pos.commission already holds the real ENTRY fee; subtract exit fee too.
        total_fees = (pos.commission or 0.0) + (exit_fee or 0.0)
        pos.pnl = gross - total_fees
        pos.commission = total_fees
        notional = pos.entry_price * pos.amount
        pos.pnl_percent = (pos.pnl / notional * 100) if notional else 0.0
        self.store.save_position(pos)

        logger.warning(
            f"[Binance] CLOSE {pos.symbol} @ {exit_price:.6f} | "
            f"reason={reason.value} | pnl=${pos.pnl:.4f} ({pos.pnl_percent:+.2f}%) | "
            f"fees=${total_fees:.4f}"
        )

    # --- Fee extraction --------------------------------------------------------

    def _extract_fee_usdt(self, response: dict, symbol: str, fill_price: float) -> float:
        """
        Sum the actual fees from a ccxt order response, converted to USDT.
        Binance often charges spot fees in the BASE asset (e.g. BTC on a BTC/USDT
        buy) unless paying with BNB, so base-asset fees are valued at fill price.
        """
        base = symbol.split('/')[0].upper()
        quote = symbol.split('/')[1].upper() if '/' in symbol else 'USDT'

        fees = response.get('fees')
        if not fees:
            single = response.get('fee')
            fees = [single] if single else []

        total = 0.0
        for fee in fees:
            if not fee:
                continue
            try:
                cost = float(fee.get('cost') or 0.0)
            except (TypeError, ValueError):
                continue
            if cost <= 0:
                continue
            cur = (fee.get('currency') or '').upper()
            if cur in (quote, 'USDT', 'USD', 'BUSD', 'USDC', ''):
                total += cost                        # already quote currency
            elif cur == base:
                total += cost * fill_price            # base asset -> USDT
            else:
                total += cost * fill_price            # best-effort fallback
        return total

    # --- Precision helpers -----------------------------------------------------

    def _amount_to_precision(self, symbol: str, amount: float) -> float:
        try:
            return float(self.exchange.amount_to_precision(symbol, amount))
        except Exception:
            return float(amount)

    def _price_to_precision(self, symbol: str, price: float) -> float:
        try:
            return float(self.exchange.price_to_precision(symbol, price))
        except Exception:
            return float(price)

    def _marketable_limit_price(self, symbol: str, side: Side,
                                fallback_price: float) -> float:
        """
        A limit price that crosses the spread so an IOC order fills immediately,
        while capping slippage to `limit_offset_pct`. Uses the live ask/bid when
        available, falling back to the signal's reference price.
        """
        ref = fallback_price
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if side == Side.BUY:
                ref = float(ticker.get('ask') or ticker.get('last') or fallback_price)
            else:
                ref = float(ticker.get('bid') or ticker.get('last') or fallback_price)
        except Exception as e:
            logger.debug(f"[Binance] ticker fetch failed for {symbol}: {e}")

        if side == Side.BUY:
            price = ref * (1 + self.limit_offset_pct)   # pay up to offset above ask
        else:
            price = ref * (1 - self.limit_offset_pct)   # accept down to offset below bid
        return self._price_to_precision(symbol, price)

    # --- Misc broker interface -------------------------------------------------

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        try:
            self.exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.error(f"[Binance] Cancel order {order_id} failed: {e}")
            return False

    def get_open_orders(self) -> List[Order]:
        try:
            return self.exchange.fetch_open_orders()
        except Exception as e:
            logger.error(f"[Binance] Fetch open orders failed: {e}")
            return []

    def get_open_position(self) -> Optional[Position]:
        """Backward compat: returns first open position."""
        return self.store.get_open_position()

    def get_open_positions(self) -> List[Position]:
        return self.store.get_open_positions()

    def get_position_for_symbol(self, symbol: str) -> Optional[Position]:
        return self.store.get_open_position_for_symbol(symbol)
