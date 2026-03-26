import random
import time
import numpy as np
from typing import List, Optional
from storage.sqlite_store import SQLiteStore
from optimize.param_sets import ARMS

class Bandit:
    def __init__(self, store: SQLiteStore, exploration_prob: float = 0.2, min_samples: int = 5):
        self.store = store
        self.epsilon = exploration_prob
        self.min_samples = min_samples
        self.n_arms = len(ARMS)
        self.counts = [0] * self.n_arms
        self.values = [0.0] * self.n_arms
        # Cache stats logic... to be loaded on startup

    def update_stats(self):
        """
        Reconstructs arm stats from the database.
        """
        try:
            conn = self.store.get_connection()
            cursor = conn.cursor()
            
            # Reset
            self.counts = [0] * self.n_arms
            self.values = [0.0] * self.n_arms
            
            # Fetch all trades with valid arm_id
            # Join trades with positions to get strategy_params/arm_id if needed, 
            # but we assume arm_id is stored in positions or arm_performance table.
            # Let's check arm_performance first.
            cursor.execute("SELECT arm_id, r_multiple FROM arm_performance")
            rows = cursor.fetchall()
            
            for row in rows:
                arm_id = int(row['arm_id'])
                r = row['r_multiple']
                
                if 0 <= arm_id < self.n_arms:
                    n = self.counts[arm_id]
                    # Update average using online avg formula or sum
                    # NewAvg = (OldAvg * n + NewVal) / (n+1)
                    current_avg = self.values[arm_id]
                    self.values[arm_id] = (current_avg * n + r) / (n + 1)
                    self.counts[arm_id] += 1
            
            conn.close()
            # print(f"Bandit Stats: {list(zip(self.counts, self.values))}")
        except Exception as e:
            print(f"Error updating bandit stats: {e}")

    def select_arm_index(self) -> int:
        self.update_stats() # Sync before choice
        
        # Epsilon-Greedy
        if random.random() < self.epsilon:
            return random.randint(0, self.n_arms - 1)
            
        # Exploit: Best mean R-multiple
        # If all zero, random
        if sum(self.counts) == 0:
             return random.randint(0, self.n_arms - 1)
             
        # Add small noise to break ties
        values_noisy = [v + random.normalvariate(0, 0.001) for v in self.values]
        return int(np.argmax(values_noisy))

    def record_outcome(self, arm_id: int, r_multiple: float, pnl_pct: float, outcome: str):
        conn = self.store.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO arm_performance (arm_id, timestamp, r_multiple, pnl_percent, outcome) VALUES (?, ?, ?, ?, ?)", 
                       (arm_id, int(time.time()*1000), r_multiple, pnl_pct, outcome))
        conn.commit()
        conn.close()

