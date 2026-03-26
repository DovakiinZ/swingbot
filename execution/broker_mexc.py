"""
MEXC broker — live trading broker for swingbot.
Uses MEXC Spot V3 API via ccxt.
MEXC does not require special 'category' params like Bybit.
"""
import os
import logging
import ccxt
from typing import List, Optional
from core.types import Signal, Order, Position, Side, OrderType, OrderStatus
from execution.broker_base import Broker
from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class MexcBroker(Broker):
    def __init__(self, store: SQLiteStore, market):
        """MEXC spot broker via ccxt."""
        self.store = store
        self.market = market

        self.exchange = ccxt.mexc({
            'apiKey': os.getenv('MEXC_API_KEY'),
            'secret': os.getenv('MEXC_API_SECRET'),
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
            }
        })

    def get_balance(self) -> float:
        """Returns available USDT balance."""
        try:
            balance = self.exchange.fetch_balance()
            usdt = balance.get('USDT') or balance.get('free', {})
            if isinstance(usdt, dict):
                return float(usdt.get('free', 0) or 0)
            return float(usdt or 0)
        except Exception as e:
            logger.error(f"[MEXC] Balance fetch failed: {e}")
            return 0.0

    def get_detailed_balance(self) -> dict:
        """Returns detailed USDT balance info."""
        try:
            balance = self.exchange.fetch_balance()
            usdt = balance.get('USDT', {})
            if not isinstance(usdt, dict):
                usdt = {}
            return {
                'USDT_free':  float(usdt.get('free', 0) or 0),
                'USDT_total': float(usdt.get('total', 0) or 0),
            }
        except Exception as e:
            logger.error(f"[MEXC] Detailed balance failed: {e}")
            return {'USDT_free': 0, 'USDT_total': 0}

    def place_order(self, signal: Signal, size: float) -> Optional[Order]:
        """Place a market order on MEXC."""
        try:
            side = 'buy' if signal.side == Side.BUY else 'sell'
            order = self.exchange.create_order(
                symbol=signal.symbol,
                type='market',
                side=side,
                amount=size,
            )
            logger.warning(
                f"[MEXC] Order placed: {side.upper()} {size} {signal.symbol} "
                f"@ ~{signal.price:.4f} | ID: {order['id']}"
            )
            result = Order(
                id=order['id'],
                symbol=signal.symbol,
                side=signal.side,
                order_type=OrderType.MARKET,
                amount=size,
                price=signal.price,
                status=OrderStatus.FILLED,
                filled_amount=size,
                filled_price=float(order.get('average', signal.price) or signal.price),
                timestamp=int(order.get('timestamp', 0) or 0)
            )
            self.store.save_order(result)
            return result
        except Exception as e:
            logger.error(f"[MEXC] Order failed: {e}")
            return None

    def get_open_position(self) -> Optional[Position]:
        """Backward compat: returns first open position."""
        positions = self.store.get_open_positions()
        return positions[0] if positions else None

    def get_open_positions(self) -> List[Position]:
        """Returns ALL open positions from store."""
        return self.store.get_open_positions()

    def get_position_for_symbol(self, symbol: str) -> Optional[Position]:
        """Returns position for a specific symbol."""
        return self.store.get_open_position_for_symbol(symbol)

    def get_open_orders(self) -> list:
        """Fetch open orders from MEXC."""
        try:
            return self.exchange.fetch_open_orders()
        except Exception as e:
            logger.error(f"[MEXC] Open orders fetch failed: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order on MEXC."""
        try:
            self.exchange.cancel_order(order_id)
            return True
        except Exception as e:
            logger.error(f"[MEXC] Cancel order failed: {e}")
            return False

    def sync(self):
        """Sync positions with exchange (placeholder)."""
        pass
