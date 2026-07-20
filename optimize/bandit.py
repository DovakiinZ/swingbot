import random
import time
import numpy as np
from typing import List, Optional
from storage.sqlite_store import SQLiteStore
from optimize.param_sets import ARMS

class Bandit:
    def __init__(self, store: SQLiteStore, min_samples: int = 5):
        self.store = store
        self.min_samples = min_samples
        self.n_arms = len(ARMS)
        self.counts = [0] * self.n_arms
        self.values = [0.0] * self.n_arms
        self.variances = [1.0] * self.n_arms # For Thompson Sampling

    def update_stats(self):
        """
        Reconstructs arm stats from the database using a Bayesian approach for Thompson Sampling.
        """
        try:
            conn = self.store.get_connection()
            cursor = conn.cursor()
            
            # Reset
            self.counts = [0] * self.n_arms
            self.values = [0.0] * self.n_arms
            self.variances = [1.0] * self.n_arms
            
            # Fetch all trades with valid arm_id
            cursor.execute("SELECT arm_id, r_multiple FROM arm_performance")
            rows = cursor.fetchall()
            
            # Temporary storage for calculating variance
            rewards = [[] for _ in range(self.n_arms)]
            
            for row in rows:
                arm_id = int(row['arm_id'])
                r = row['r_multiple']
                if 0 <= arm_id < self.n_arms:
                    rewards[arm_id].append(r)
            
            for i in range(self.n_arms):
                n = len(rewards[i])
                self.counts[i] = n
                if n > 0:
                    self.values[i] = np.mean(rewards[i])
                    # Posterior variance for the mean: sigma^2 / n
                    # We use a simple approximation: 1 / (n + 1)
                    self.variances[i] = 1.0 / (n + 1.0)
                else:
                    self.values[i] = 0.0
                    self.variances[i] = 1.0 # High uncertainty
            
            conn.close()
        except Exception as e:
            print(f"Error updating bandit stats: {e}")

    def select_arm_index(self) -> int:
        self.update_stats()
        
        # Thompson Sampling: Sample from the posterior distribution of each arm
        samples = []
        for i in range(self.n_arms):
            # Sample from N(mu_i, sigma_i^2)
            sample = np.random.normal(self.values[i], np.sqrt(self.variances[i]))
            samples.append(sample)
            
        return int(np.argmax(samples))

    def record_outcome(self, arm_id: int, r_multiple: float, pnl_pct: float, outcome: str):
        conn = self.store.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO arm_performance (arm_id, timestamp, r_multiple, pnl_percent, outcome) VALUES (?, ?, ?, ?, ?)", 
                       (arm_id, int(time.time()*1000), r_multiple, pnl_pct, outcome))
        conn.commit()
        conn.close()
