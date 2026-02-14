import sqlite3
import json
from typing import List, Optional, Dict, Any
from core.types import Candle, Order, Position, Trade, OrderStatus, PositionStatus

class SQLiteStore:
    def __init__(self, db_path: str = "swingbot.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with open('storage/schema.sql', 'r') as f:
            schema = f.read()
        conn = sqlite3.connect(self.db_path)
        conn.executescript(schema)
        conn.close()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_candles(self, candles: List[Candle], symbol: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        data = [(symbol, c.timestamp, c.open, c.high, c.low, c.close, c.volume) for c in candles]
        cursor.executemany("""
            INSERT OR REPLACE INTO candles (symbol, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, data)
        conn.commit()
        conn.close()

    def get_latest_candles(self, symbol: str, limit: int = 500) -> List[Candle]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, open, high, low, close, volume 
            FROM candles WHERE symbol = ? 
            ORDER BY timestamp DESC LIMIT ?
        """, (symbol, limit))
        rows = cursor.fetchall()
        conn.close()
        
        candles = []
        for row in reversed(rows): # Return in chronological order (oldest first)
            candles.append(Candle(
                timestamp=row['timestamp'],
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume']
            ))
        return candles

    def save_order(self, order: Order):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO orders (id, symbol, side, order_type, amount, price, status, filled_amount, filled_price, timestamp, client_order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (order.id, order.symbol, order.side.value, order.order_type.value, order.amount, order.price, order.status.value, order.filled_amount, order.filled_price, order.timestamp, order.client_order_id))
        conn.commit()
        conn.close()

    def get_open_orders(self) -> List[Order]:
        # Implementation skipped for brevity, similar to get_latest_candles but mapping back to Order object
        pass

    def save_position(self, position: Position):
        conn = self.get_connection()
        cursor = conn.cursor()
        params_json = json.dumps(position.strategy_params.to_dict()) if position.strategy_params else None
        
        cursor.execute("""
            INSERT OR REPLACE INTO positions (id, symbol, side, entry_price, amount, stop_loss, take_profit, entry_time, status, exit_price, exit_time, exit_reason, pnl, pnl_percent, commission, strategy_params)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (position.id, position.symbol, position.side.value, position.entry_price, position.amount, position.stop_loss, position.take_profit, position.entry_time, position.status.value, position.exit_price, position.exit_time, position.exit_reason.value if position.exit_reason else None, position.pnl, position.pnl_percent, position.commission, params_json))
        conn.commit()
        conn.close()

    def get_open_position(self) -> Optional[Position]:
         # Implementation to fetch status='OPEN'
         pass

    # Add other methods as needed for trades, stats, etc.
