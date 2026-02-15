import sqlite3
import json
from typing import List, Optional, Dict, Any
from core.types import Candle, Order, Position, Trade, OrderStatus, PositionStatus, Side, OrderType, Reason

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
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM positions WHERE status = 'OPEN'")
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
            
        return Position(
            id=row['id'],
            symbol=row['symbol'],
            side=Side(row['side']),
            entry_price=row['entry_price'],
            amount=row['amount'],
            stop_loss=row['stop_loss'],
            take_profit=row['take_profit'],
            entry_time=row['entry_time'],
            status=PositionStatus(row['status']),
            exit_price=row['exit_price'],
            exit_time=row['exit_time'],
            exit_reason=Reason(row['exit_reason']) if row['exit_reason'] else None,
            pnl=row['pnl'],
            pnl_percent=row['pnl_percent'],
            commission=row['commission'],
            strategy_params=None # Can reload if needed, simplified for now
        )

    def get_open_orders(self) -> List[Order]:
        conn = self.get_connection()
        cursor = conn.cursor()
        # Fetch orders that are not closed (FILLED, CANCELED, REJECTED, EXPIRED)
        # So PENDING or OPEN
        cursor.execute("SELECT * FROM orders WHERE status IN ('PENDING', 'OPEN')")
        rows = cursor.fetchall()
        conn.close()
        
        orders = []
        for row in rows:
            orders.append(Order(
                id=row['id'],
                symbol=row['symbol'],
                side=Side(row['side']),
                order_type=OrderType(row['order_type']),
                amount=row['amount'],
                price=row['price'],
                status=OrderStatus(row['status']),
                filled_amount=row['filled_amount'],
                filled_price=row['filled_price'],
                timestamp=row['timestamp'],
                client_order_id=row['client_order_id']
            ))
        return orders

    def update_order_status(self, order_id: str, status: OrderStatus, filled_amount: float = 0.0, filled_price: float = 0.0):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE orders 
            SET status = ?, filled_amount = ?, filled_price = ?
            WHERE id = ?
        """, (status.value, filled_amount, filled_price, order_id))
        conn.commit()
        conn.close()
        
    def get_daily_stats(self, date_str: str) -> Dict[str, Any]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (date_str,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
        return {}

    def update_daily_stats(self, date_str: str, updates: Dict[str, Any]):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Upsert logic basics
        # Check exist
        cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (date_str,))
        exists = cursor.fetchone()
        
        if not exists:
            cursor.execute("""
                INSERT INTO daily_stats (date, pnl, trades_count, wins, losses, max_drawdown, start_balance, end_balance, paused_until)
                VALUES (?, 0.0, 0, 0, 0, 0.0, 0.0, 0.0, NULL)
            """, (date_str,))
            
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [date_str]
        
        cursor.execute(f"UPDATE daily_stats SET {set_clause} WHERE date = ?", values)
        conn.commit()
        conn.close()

    def get_daily_trade_stats(self, date_str: str) -> Dict[str, Any]:
        """
        Calculate stats from CLOSED orders/trades for a specific day?
        Actually, we can aggregate from the 'trades' table where timestamp falls in that day.
        Or 'positions' table where exit_time falls in that day.
        Let's use positions table for closed positions today.
        """
        start_ts = int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=None).timestamp() * 1000)
        end_ts = start_ts + 86400 * 1000
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Get closed positions in this time range
        cursor.execute("""
            SELECT * FROM positions 
            WHERE status = 'CLOSED' 
            AND exit_time >= ? AND exit_time < ?
        """, (start_ts, end_ts))
        
        rows = cursor.fetchall()
        conn.close()
        
        count = len(rows)
        if count == 0:
            return {
                "count": 0, "pnl": 0.0, "winrate": 0.0, 
                "expectancy": 0.0, "max_dd": 0.0, "best_arm": "-"
            }
            
        total_pnl = 0.0
        wins = 0
        total_r = 0.0 # For simplicity, approximate expectancy via PnL or R-multiples if available
        # To get proper expectancy (Avg Win * Win% - Avg Loss * Loss%), we need raw PnL values.
        
        pnls = []
        arm_counts = {}
        
        for row in rows:
            pnl = row['pnl']
            pnls.append(pnl)
            total_pnl += pnl
            if pnl > 0: wins += 1
            
            # Extract arm from params if able, or just track 'strategy_params' raw string as key
            # It's stored as JSON string usually or null.
            # Ideally we saved arm_id in positions (we added it to schema).
            # Let's try to get arm_id if it exists in schema now (we added it in previous turn)
            # Row is sqlite3.Row object
            if 'arm_id' in row.keys() and row['arm_id'] is not None:
                arm = row['arm_id']
                arm_counts[arm] = arm_counts.get(arm, 0) + 1
        
        winrate = (wins / count) * 100
        avg_pnl = total_pnl / count
        # Expectancy = Average PnL (simple definition)
        
        # Best Arm
        best_arm = "-"
        if arm_counts:
            best_arm = max(arm_counts, key=arm_counts.get)
            
        # Drawdown calculation (intra-day based on closed sequence)
        # Scan cumulative PnL to find max drawdown from peak
        cum_pnl = 0.0
        peak = 0.0
        max_dd_val = 0.0
        
        for p in pnls:
            cum_pnl += p
            if cum_pnl > peak: peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_dd_val: max_dd_val = dd
            
        # Convert max_dd_val to approximate percent? 
        # Needs start balance. Let's return raw value or 0 for now if balance unknown in this scope.
        # Or just return 0 if no balance info.
        
        return {
            "best_arm": best_arm
        }

    def save_polymarket_snapshot(self, timestamp: int, market_key: str, probability: float, risk_scale: float):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO polymarket_snapshots (timestamp, market_key, probability, risk_scale)
            VALUES (?, ?, ?, ?)
        """, (timestamp, market_key, probability, risk_scale))
        conn.commit()
        conn.close()

    def get_latest_polymarket_snapshot(self) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM polymarket_snapshots 
            ORDER BY timestamp DESC LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None



