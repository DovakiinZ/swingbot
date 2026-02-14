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
        self._position: Optional[Position] = None
        
    def get_balance(self) -> float:
        try:
            balance = self.market.exchange.fetch_balance()
            # Use free balance for trading? Or total? 
            # Usually we risk based on Total Equity (free + used). 
            # Let's return total for risk calc.
            return float(balance['USDT']['total'])
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return 0.0

    def get_detailed_balance(self) -> Dict[str, float]:
        """
        Returns {
            'USDT_free': float,
            'USDT_total': float,
            'BTC_total': float
        }
        """
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
        # Implementation of live order placement via ccxt
        # 1. Place Market Order
        # 2. Record it
        # 3. If Entry, place OCO or Stop-Limit for SL/TP?
        # For simplicity/safety, we might just track SL/TP internally and market exit, 
        # OR place a Stop Market if Binance supports it (often Stop Limit).
        
        try:
            side = 'buy' if signal.side == Side.BUY else 'sell'
            response = self.market.exchange.create_order(
                symbol=signal.symbol,
                type='market',
                side=side,
                amount=size
            )
            
            # Parse response to Order object
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
        # Not typically using pending orders in this simple market-order logic
        # But required for OCO/Stop orders
        return True

    def get_open_orders(self) -> List[Order]:
        return []

    def get_open_position(self) -> Optional[Position]:
        # Needed: Logic to reconcile with Exchange or trust DB?
        # Trust DB + Verification
        return self.store.get_open_position()

    def sync(self):
        # Fetch open orders and positions from Binance and update DB
        pass
