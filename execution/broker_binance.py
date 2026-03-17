from typing import List, Optional, Dict
from core.types import Order, Position, Signal, Side, OrderType, OrderStatus, PositionStatus, Reason, Candle
from data.market import MarketData
from execution.broker_base import Broker
from storage.sqlite_store import SQLiteStore
import logging

logger = logging.getLogger(__name__)

class BinanceBroker(Broker):
    def __init__(self,
                 store: SQLiteStore,
                 market: MarketData):
        self.store = store
        self.market = market

    def get_balance(self) -> float:
        try:
            balance = self.market.exchange.fetch_balance()
            return float(balance['USDT']['total'])
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return 0.0

    def get_detailed_balance(self) -> Dict[str, float]:
        try:
            bal = self.market.exchange.fetch_balance()
            return {
                'USDT_free': float(bal.get('USDT', {}).get('free', 0.0)),
                'USDT_total': float(bal.get('USDT', {}).get('total', 0.0)),
                'BTC_total': float(bal.get('BTC', {}).get('total', 0.0))
            }
        except Exception as e:
            logger.error(f"Error fetching detailed balance: {e}")
            return {'USDT_free': 0.0, 'USDT_total': 0.0, 'BTC_total': 0.0}

    def place_order(self, signal: Signal, size: float) -> Optional[Order]:
        try:
            side = 'buy' if signal.side == Side.BUY else 'sell'
            response = self.market.exchange.create_order(
                symbol=signal.symbol,
                type='market',
                side=side,
                amount=size
            )

            filled_qty = float(response['filled']) if 'filled' in response else 0.0
            avg_price = float(response['average']) if 'average' in response else 0.0

            order = Order(
                id=str(response['id']),
                symbol=signal.symbol,
                side=signal.side,
                order_type=OrderType.MARKET,
                amount=size,
                price=signal.price,
                status=OrderStatus.FILLED if response['status'] == 'closed' else OrderStatus.OPEN,
                filled_amount=filled_qty,
                filled_price=avg_price,
                timestamp=int(response['timestamp']),
                client_order_id=response.get('clientOrderId')
            )

            self.store.save_order(order)
            return order

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_open_orders(self) -> List[Order]:
        return []

    def get_open_position(self) -> Optional[Position]:
        """Backward compat: returns first open position."""
        return self.store.get_open_position()

    def get_open_positions(self) -> List[Position]:
        return self.store.get_open_positions()

    def get_position_for_symbol(self, symbol: str) -> Optional[Position]:
        return self.store.get_open_position_for_symbol(symbol)

    def sync(self):
        pass
