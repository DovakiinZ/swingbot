"""
Bybit broker -- primary live trading broker for swingbot.
Uses Bybit Unified Trading Account (UTA) via ccxt.
Supports both spot and linear futures via category param.
Bybit v5 API requires 'category' on all order endpoints.
"""
import os
import logging
import ccxt
from typing import List, Optional
from core.types import Signal, Order, Position, Side, OrderType, OrderStatus, PositionStatus, Reason
from execution.broker_base import Broker
from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class BybitBroker(Broker):
    def __init__(self, store: SQLiteStore, market, account_type: str = 'spot'):
        """
        account_type: 'spot' for spot trading, 'linear' for USDT perpetual futures
        """
        self.store = store
        self.market = market
        self.account_type = account_type  # 'spot' or 'linear'

        self.exchange = ccxt.bybit({
            'apiKey': os.getenv('BYBIT_API_KEY'),
            'secret': os.getenv('BYBIT_API_SECRET'),
            'enableRateLimit': True,
            'options': {
                'defaultType': account_type,
                'accountType': 'UNIFIED',
            }
        })

    def get_balance(self) -> float:
        """Returns available USDT balance."""
        try:
            balance = self.exchange.fetch_balance()
            return float(balance['USDT']['free'] or 0)
        except Exception as e:
            logger.error(f"[Bybit] Balance fetch failed: {e}")
            return 0.0

    def get_detailed_balance(self) -> dict:
        """Returns detailed USDT balance info."""
        try:
            balance = self.exchange.fetch_balance()
            return {
                'USDT_free':  float(balance['USDT'].get('free', 0) or 0),
                'USDT_total': float(balance['USDT'].get('total', 0) or 0),
            }
        except Exception as e:
            logger.error(f"[Bybit] Detailed balance failed: {e}")
            return {'USDT_free': 0, 'USDT_total': 0}

    def place_order(self, signal: Signal, size: float) -> Optional[Order]:
        """
        Place a market order on Bybit.
        Bybit v5 API requires 'category' parameter.
        """
        try:
            side = 'buy' if signal.side == Side.BUY else 'sell'
            order = self.exchange.create_order(
                symbol=signal.symbol,
                type='market',
                side=side,
                amount=size,
                params={'category': self.account_type}
            )
            logger.warning(
                f"[Bybit] Order placed: {side.upper()} {size} {signal.symbol} "
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
            logger.error(f"[Bybit] Order failed: {e}")
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
        """Fetch open orders from Bybit."""
        try:
            return self.exchange.fetch_open_orders(
                params={'category': self.account_type}
            )
        except Exception as e:
            logger.error(f"[Bybit] Open orders fetch failed: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order on Bybit."""
        try:
            self.exchange.cancel_order(
                order_id,
                params={'category': self.account_type}
            )
            return True
        except Exception as e:
            logger.error(f"[Bybit] Cancel order failed: {e}")
            return False

    def sync(self):
        """Sync positions with exchange (placeholder)."""
        pass
