import sys
import os
import sqlite3
from datetime import datetime

# Pass checks if we can import our store and run basic operations
try:
    from storage.sqlite_store import SQLiteStore
    from core.types import Candle, Side, Order, Position, OrderType, OrderStatus, PositionStatus
except ImportError:
    print("Error: Could not import swingbot modules. Run from project root.")
    sys.exit(1)

def check_store():
    print("Checking SQLiteStore...")
    
    db_path = "test_store.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        
    store = SQLiteStore(db_path=db_path)
    
    # 1. Check Tables
    conn = store.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor.fetchall()]
    required = {'candles', 'orders', 'positions', 'trades', 'daily_stats', 'arm_performance'}
    
    missing = required - set(tables)
    if missing:
        print(f"FAIL: Missing tables: {missing}")
        return False
    else:
        print("PASS: Schema tables created.")
        
    # 2. Check Order CRUD
    order = Order(
        id="test_ord_1",
        symbol="BTC/USDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        amount=0.1,
        price=50000,
        status=OrderStatus.PENDING,
        timestamp=1000,
        client_order_id="cid_1"
    )
    store.save_order(order)
    
    orders = store.get_open_orders()
    if len(orders) == 1 and orders[0].id == "test_ord_1":
        print("PASS: save_order / get_open_orders")
    else:
        print(f"FAIL: Order retrieval mismatch. Got {len(orders)}")
        return False
        
    # 3. Check Position CRUD
    pos = Position(
        id="pos_1",
        symbol="BTC/USDT",
        side=Side.BUY,
        entry_price=50000,
        amount=0.1,
        stop_loss=49000,
        take_profit=52000,
        entry_time=1000,
        status=PositionStatus.OPEN,
        pnl=0,
        pnl_percent=0,
        commission=0
    )
    store.save_position(pos)
    
    fetched_pos = store.get_open_position()
    if fetched_pos and fetched_pos.id == "pos_1":
        print("PASS: save_position / get_open_position")
    else:
        print("FAIL: Position retrieval failed.")
        return False
        
    # Cleanup
    conn.close()
    if os.path.exists(db_path):
        os.remove(db_path)
        
    print("ALL CHECKS PASSED.")
    return True

if __name__ == "__main__":
    success = check_store()
    sys.exit(0 if success else 1)
