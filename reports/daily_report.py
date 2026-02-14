import json
import os
from datetime import datetime
from typing import Dict, Any
from storage.sqlite_store import SQLiteStore
from core.utils import save_json

class DailyReport:
    def __init__(self, store: SQLiteStore, report_dir: str = "reports/out"):
        self.store = store
        self.report_dir = report_dir
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)

    def generate(self, date_str: str = None) -> Dict[str, Any]:
        """
        Generate daily report for the given date (YYYY-MM-DD).
        If no date, defaults to today.
        """
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
            
        conn = self.store.get_connection()
        cursor = conn.cursor()
        
        # 1. Fetch Trades for the day
        start_ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
        end_ts = start_ts + 86400000
        
        cursor.execute("""
            SELECT * FROM trades 
            WHERE timestamp >= ? AND timestamp < ?
        """, (start_ts, end_ts))
        trades = cursor.fetchall()
        
        # 2. Daily Stats
        pnl = sum([t['amount'] * (t['price'] - 0) * (1 if t['side']=='SELL' else -1) for t in trades]) 
        # Wait, PnL is only realized on closed positions. 
        # Better to query closed positions with exit_time in range.
        
        cursor.execute("""
            SELECT * FROM positions 
            WHERE status = 'CLOSED' AND exit_time >= ? AND exit_time < ?
        """, (start_ts, end_ts))
        closed_positions = cursor.fetchall()
        
        daily_pnl = sum([p['pnl'] for p in closed_positions])
        wins = len([p for p in closed_positions if p['pnl'] > 0])
        losses = len([p for p in closed_positions if p['pnl'] <= 0])
        total_closed = len(closed_positions)
        win_rate = (wins/total_closed * 100) if total_closed > 0 else 0.0
        
        report = {
            "date": date_str,
            "pnl_usdt": daily_pnl,
            "trades_count": total_closed,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "positions_closed": [dict(p) for p in closed_positions]
        }
        
        # Save JSON
        filepath = os.path.join(self.report_dir, f"report_{date_str}.json")
        save_json(filepath, report)
        
        conn.close()
        return report

    def print_summary(self, report: Dict[str, Any]):
        print("\n" + "="*40)
        print(f" DAILY REPORT: {report['date']}")
        print("="*40)
        print(f"PnL: {report['pnl_usdt']:.2f} USDT")
        print(f"Trades: {report['trades_count']} (W: {report['wins']} / L: {report['losses']})")
        print(f"Win Rate: {report['win_rate']:.1f}%")
        print("="*40 + "\n")
