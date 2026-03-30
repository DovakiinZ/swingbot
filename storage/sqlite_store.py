import sqlite3
import json
import uuid
import time
from datetime import datetime
from typing import List, Optional, Dict, Any
from core.types import Candle, Order, Position, Trade, OrderStatus, PositionStatus, Side, OrderType, Reason, ScanResult

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


class SQLiteStore:
    def __init__(self, db_path: str = "swingbot.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with open('storage/schema.sql', 'r', encoding='utf-8') as f:
            schema = f.read()
        conn = sqlite3.connect(self.db_path)
        conn.executescript(schema)
        # Migrate: add triple-barrier columns if missing
        self._migrate_tb_columns(conn)
        conn.close()

    def _migrate_tb_columns(self, conn):
        """Add triple-barrier and newer columns to trade_features if they don't exist."""
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(trade_features)")
        existing = {row[1] for row in cursor.fetchall()}
        new_cols = {
            'tb_label': 'INTEGER',
            'tb_hours_to_barrier': 'REAL',
            'tb_barrier_hit': 'TEXT',
            'tb_upper_barrier': 'REAL',
            'tb_lower_barrier': 'REAL',
            'tb_return_pct': 'REAL',
            'btc_correlation': 'REAL',  # Altcoin-BTC correlation feature
        }
        for col, col_type in new_cols.items():
            if col not in existing:
                try:
                    cursor.execute(f"ALTER TABLE trade_features ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass
        conn.commit()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # --- Candles ---------------------------------------------------------------

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
        for row in reversed(rows):
            candles.append(Candle(
                timestamp=row['timestamp'],
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume']
            ))
        return candles

    # --- Orders ----------------------------------------------------------------

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
        conn = self.get_connection()
        cursor = conn.cursor()
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

    # --- Positions -------------------------------------------------------------

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

    def _row_to_position(self, row) -> Position:
        """Convert a database row to a Position object."""
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
            strategy_params=None
        )

    def get_open_position(self) -> Optional[Position]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM positions WHERE status = 'OPEN'")
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None
        return self._row_to_position(row)

    def get_open_positions(self) -> List[Position]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM positions WHERE status = 'OPEN'")
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_position(row) for row in rows]

    def get_open_position_for_symbol(self, symbol: str) -> Optional[Position]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM positions WHERE status = 'OPEN' AND symbol = ?", (symbol,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return self._row_to_position(row)

    def get_closed_trades_for_date(self, date_str: str) -> List[Dict[str, Any]]:
        """Get closed positions for a specific date."""
        start_ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
        end_ts = start_ts + 86400000

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM positions
            WHERE status = 'CLOSED' AND exit_time >= ? AND exit_time < ?
        """, (start_ts, end_ts))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # --- Scan Results ----------------------------------------------------------

    def save_scan_results(self, results: List[ScanResult]):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scan_results")
        data = [(r.symbol, r.score, r.rsi, r.atr_pct, r.volume_rank, r.trend, r.regime, r.scanned_at) for r in results]
        cursor.executemany("""
            INSERT INTO scan_results (symbol, score, rsi, atr_pct, volume_rank, trend, regime, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, data)
        conn.commit()
        conn.close()

    def get_latest_scan_results(self) -> List[ScanResult]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scan_results ORDER BY score DESC")
        rows = cursor.fetchall()
        conn.close()

        return [ScanResult(
            symbol=row['symbol'],
            score=row['score'],
            rsi=row['rsi'],
            atr_pct=row['atr_pct'],
            volume_rank=row['volume_rank'],
            trend=row['trend'],
            regime=row['regime'],
            scanned_at=row['scanned_at']
        ) for row in rows]

    # --- Daily Stats -----------------------------------------------------------

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

        cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (date_str,))
        exists = cursor.fetchone()

        if not exists:
            cursor.execute("""
                INSERT INTO daily_stats (date, pnl, trades_count, wins, losses, max_drawdown, start_balance, end_balance, paused_until, peak_balance)
                VALUES (?, 0.0, 0, 0, 0, 0.0, 0.0, 0.0, NULL, 0.0)
            """, (date_str,))

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [date_str]

        cursor.execute(f"UPDATE daily_stats SET {set_clause} WHERE date = ?", values)
        conn.commit()
        conn.close()

    def get_daily_trade_stats(self, date_str: str) -> Dict[str, Any]:
        """Calculate stats from closed positions for a specific day."""
        start_ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
        end_ts = start_ts + 86400 * 1000

        conn = self.get_connection()
        cursor = conn.cursor()
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

        pnls = []
        wins = 0
        arm_counts = {}

        for row in rows:
            pnl = row['pnl']
            pnls.append(pnl)
            if pnl > 0:
                wins += 1
            if 'arm_id' in row.keys() and row['arm_id'] is not None:
                arm = row['arm_id']
                arm_counts[arm] = arm_counts.get(arm, 0) + 1

        total_pnl = sum(pnls)
        winrate = (wins / count) * 100
        avg_pnl = total_pnl / count

        best_arm = "-"
        if arm_counts:
            best_arm = max(arm_counts, key=arm_counts.get)

        cum_pnl = 0.0
        peak = 0.0
        max_dd_val = 0.0
        for p in pnls:
            cum_pnl += p
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_dd_val:
                max_dd_val = dd

        return {
            "count":       count,
            "pnl":         total_pnl,
            "winrate":     winrate,
            "expectancy":  avg_pnl,
            "max_dd":      max_dd_val,
            "best_arm":    best_arm,
        }

    # --- Peak Balance ----------------------------------------------------------

    def get_peak_balance(self) -> float:
        """Get the all-time peak balance from daily stats."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(peak_balance) as peak FROM daily_stats")
        row = cursor.fetchone()
        conn.close()
        if row and row['peak']:
            return float(row['peak'])
        return 0.0

    def update_peak_balance(self, date_str: str, balance: float):
        """Update peak balance if current balance exceeds it."""
        current_peak = self.get_peak_balance()
        if balance > current_peak:
            self.update_daily_stats(date_str, {'peak_balance': balance})

    # --- Polymarket ------------------------------------------------------------

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

    # --- Trade Features (ML Training Data) -------------------------------------

    def save_trade_features(self, features: dict) -> None:
        """Save feature snapshot at trade entry for AI training."""
        conn = self.get_connection()
        cursor = conn.cursor()

        feature_id = str(uuid.uuid4())
        captured_at = int(time.time())

        cursor.execute("""
            INSERT INTO trade_features (
                id, trade_id, symbol, price,
                rsi_14, rsi_7, macd, macd_signal, macd_hist,
                ema_fast, ema_slow, ema_fast_slope, ema_slow_slope, adx,
                atr, atr_percent, bb_position, bb_width,
                volume_ratio, scanner_score, breakout_detected,
                fear_greed, macro_scale,
                hour_of_day, day_of_week,
                captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            feature_id,
            features.get('trade_id', ''),
            features.get('symbol', ''),
            features.get('price', 0),
            features.get('rsi_14', 0),
            features.get('rsi_7', 0),
            features.get('macd', 0),
            features.get('macd_signal', 0),
            features.get('macd_hist', 0),
            features.get('ema_fast', 0),
            features.get('ema_slow', 0),
            features.get('ema_fast_slope', 0),
            features.get('ema_slow_slope', 0),
            features.get('adx', 0),
            features.get('atr', 0),
            features.get('atr_percent', 0),
            features.get('bb_position', 0),
            features.get('bb_width', 0),
            features.get('volume_ratio', 0),
            features.get('scanner_score', 0),
            features.get('breakout_detected', 0),
            features.get('fear_greed', 0),
            features.get('macro_scale', 0),
            features.get('hour_of_day', 0),
            features.get('day_of_week', 0),
            captured_at
        ))
        conn.commit()
        conn.close()

    def update_trade_outcome(self, trade_id: str, outcome: int, pnl: float,
                              pnl_pct: float, exit_reason: str, hold_hours: float = 0) -> None:
        """Update outcome fields for a trade feature record."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trade_features
            SET outcome = ?, pnl = ?, pnl_percent = ?, exit_reason = ?, hold_hours = ?
            WHERE trade_id = ?
        """, (outcome, pnl, pnl_pct, exit_reason, hold_hours, trade_id))
        conn.commit()
        conn.close()

    def get_training_data(self, min_samples: int = 50) -> Optional[Any]:
        """
        Returns DataFrame of completed trades with features and outcomes.
        Returns None if insufficient samples.
        """
        if not HAS_PANDAS:
            return None

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM trade_features
            WHERE outcome IS NOT NULL
            ORDER BY captured_at
        """)
        rows = cursor.fetchall()
        conn.close()

        if len(rows) < min_samples:
            return None

        df = pd.DataFrame([dict(row) for row in rows])
        return df

    def get_last_n_trades(self, n: int = 10) -> list:
        """Get the last N closed trades, most recent first."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM positions
            WHERE status = 'CLOSED'
            ORDER BY exit_time DESC
            LIMIT ?
        """, (n,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # --- Overall Stats ----------------------------------------------------------

    def get_overall_stats(self) -> Dict[str, Any]:
        """Get overall bot statistics for goal tracker and projections."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl
            FROM positions WHERE status = 'CLOSED'
        """)
        row = cursor.fetchone()
        conn.close()

        total = row['total'] or 0
        wins = row['wins'] or 0
        total_pnl = row['total_pnl'] or 0
        avg_pnl = row['avg_pnl'] or 0

        return {
            'total_trades': total,
            'wins': wins,
            'losses': total - wins,
            'total_pnl': round(total_pnl, 2),
            'avg_pnl': round(avg_pnl, 4),
            'win_rate': round((wins / total * 100) if total > 0 else 0, 1),
        }

    # --- Committee History -----------------------------------------------------

    def get_committee_history(self, limit: int = 50, symbol: str = None) -> list:
        """
        Returns committee decisions with trade outcomes joined in.
        Reads from committee_decisions table, joins with positions for outcome.
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        query = """
            SELECT cd.*,
                   p.pnl as trade_pnl,
                   p.status as trade_status,
                   CASE WHEN p.pnl > 0 THEN 'WIN'
                        WHEN p.pnl < 0 THEN 'LOSS'
                        WHEN p.pnl = 0 THEN 'BREAK_EVEN'
                        ELSE NULL END as trade_outcome
            FROM committee_decisions cd
            LEFT JOIN positions p ON cd.trade_id = p.id
        """
        params = []
        if symbol:
            query += " WHERE cd.symbol = ?"
            params.append(symbol)
        query += " ORDER BY cd.timestamp DESC LIMIT ?"
        params.append(limit)

        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception:
            conn.close()
            return []

    def save_committee_decision(self, decision: dict) -> None:
        """Save a committee decision record."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO committee_decisions (
                    id, timestamp, symbol, approved, final_score,
                    size_multiplier, veto_by, veto_reason, verdicts_json,
                    trade_executed, trade_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision.get('id', str(uuid.uuid4())),
                decision.get('timestamp', int(time.time())),
                decision.get('symbol', ''),
                1 if decision.get('approved', False) else 0,
                decision.get('final_score', 0),
                decision.get('size_multiplier', 1.0),
                decision.get('veto_by'),
                decision.get('veto_reason'),
                json.dumps(decision.get('verdicts', {})),
                1 if decision.get('trade_executed', False) else 0,
                decision.get('trade_id'),
            ))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def get_agent_accuracy(self) -> dict:
        """
        For each agent, count how many times their recommendation
        matched the actual trade outcome.
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT cd.verdicts_json,
                       CASE WHEN p.pnl > 0 THEN 1 ELSE 0 END as won
                FROM committee_decisions cd
                JOIN positions p ON cd.trade_id = p.id
                WHERE p.status = 'CLOSED' AND cd.trade_executed = 1
            """)
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            conn.close()
            return {}

        if len(rows) < 10:
            return {}

        agent_stats: Dict[str, Dict[str, int]] = {}

        for row in rows:
            try:
                verdicts = json.loads(row['verdicts_json'] or '{}')
            except (json.JSONDecodeError, TypeError):
                continue

            won = row['won']
            for agent_name, verdict in verdicts.items():
                if agent_name not in agent_stats:
                    agent_stats[agent_name] = {'decisions': 0, 'correct': 0}
                agent_stats[agent_name]['decisions'] += 1

                rec = verdict.get('rec', '').upper() if isinstance(verdict, dict) else ''
                # Agent was "correct" if they said BUY and trade won
                if (rec in ('BUY', 'SELL') and won) or (rec in ('HOLD', 'SKIP') and not won):
                    agent_stats[agent_name]['correct'] += 1

        result = {}
        for agent, stats in agent_stats.items():
            acc = (stats['correct'] / stats['decisions'] * 100) if stats['decisions'] > 0 else 0
            result[agent] = {
                'decisions': stats['decisions'],
                'correct': stats['correct'],
                'accuracy': round(acc, 1),
            }

        return result

    def get_veto_stats(self) -> dict:
        """
        How many vetoes happened, by whom, and how many
        of those vetoes were 'correct' (trade would have lost).
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT veto_by, COUNT(*) as cnt
                FROM committee_decisions
                WHERE veto_by IS NOT NULL
                GROUP BY veto_by
            """)
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            conn.close()
            return {'total_vetoes': 0, 'by_agent': {}}

        total = sum(row['cnt'] for row in rows)
        by_agent = {row['veto_by']: row['cnt'] for row in rows}

        return {
            'total_vetoes': total,
            'by_agent': by_agent,
        }

    # --- Triple-Barrier Labels -------------------------------------------------

    def update_trade_barrier_label(self, trade_id: str, tb_data: dict) -> None:
        """Update triple-barrier label fields for a trade feature record."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trade_features
            SET tb_label = ?, tb_hours_to_barrier = ?, tb_barrier_hit = ?,
                tb_upper_barrier = ?, tb_lower_barrier = ?, tb_return_pct = ?
            WHERE trade_id = ?
        """, (
            tb_data.get('tb_label'),
            tb_data.get('tb_hours_to_barrier'),
            tb_data.get('tb_barrier_hit'),
            tb_data.get('tb_upper_barrier'),
            tb_data.get('tb_lower_barrier'),
            tb_data.get('tb_return_pct'),
            trade_id
        ))
        conn.commit()
        conn.close()

    def get_triple_barrier_stats(self) -> dict:
        """Get aggregate triple-barrier labeling statistics."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as total_labeled,
                SUM(CASE WHEN tb_label = 1 THEN 1 ELSE 0 END) as upper_hits,
                SUM(CASE WHEN tb_label = -1 THEN 1 ELSE 0 END) as lower_hits,
                SUM(CASE WHEN tb_label = 0 THEN 1 ELSE 0 END) as time_hits,
                AVG(CASE WHEN tb_barrier_hit = 'upper' THEN tb_hours_to_barrier END) as avg_hours_to_tp,
                AVG(CASE WHEN tb_barrier_hit = 'lower' THEN tb_hours_to_barrier END) as avg_hours_to_sl,
                MIN(CASE WHEN tb_barrier_hit = 'upper' THEN tb_hours_to_barrier END) as fastest_win_hours
            FROM trade_features
            WHERE tb_label IS NOT NULL
        """)
        row = cursor.fetchone()
        conn.close()

        if not row or not row['total_labeled']:
            return {
                'total_labeled': 0, 'upper_hits': 0, 'lower_hits': 0,
                'time_hits': 0, 'upper_hit_pct': 0, 'avg_hours_to_tp': 0,
                'avg_hours_to_sl': 0, 'fastest_win_hours': 0, 'labels_ready': False
            }

        total = row['total_labeled']
        upper = row['upper_hits'] or 0
        return {
            'total_labeled': total,
            'upper_hits': upper,
            'lower_hits': row['lower_hits'] or 0,
            'time_hits': row['time_hits'] or 0,
            'upper_hit_pct': round(upper / total * 100, 1) if total > 0 else 0,
            'avg_hours_to_tp': round(row['avg_hours_to_tp'] or 0, 1),
            'avg_hours_to_sl': round(row['avg_hours_to_sl'] or 0, 1),
            'fastest_win_hours': round(row['fastest_win_hours'] or 0, 1),
            'labels_ready': total >= 30
        }

    def get_training_data_count(self) -> int:
        """Returns number of completed labeled training samples."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM trade_features WHERE outcome IS NOT NULL")
        row = cursor.fetchone()
        conn.close()
        return int(row['cnt']) if row else 0
