"""
Per-pair edge tracker — tracks historical profitability per symbol.

Assigns each symbol an "edge score" based on its own trade history:
  - win_rate
  - avg win / avg loss ratio
  - total expectancy
  - sample size

Used to bias the scanner toward symbols that actually make money for us,
and deprioritize symbols that consistently lose.

Inspired by Freqtrade's Edge positioning.
"""
import logging
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SymbolEdge:
    """Edge statistics for a single symbol."""
    symbol: str
    trades: int
    wins: int
    losses: int
    win_rate: float          # 0-100%
    avg_win_pct: float       # average win as %
    avg_loss_pct: float      # average loss as % (positive number)
    expectancy: float        # avg $ per trade
    edge_score: float        # -1.0 to +1.0 (higher = better)
    confidence: float        # 0-1, how confident we are (scales with sample size)

    @property
    def is_positive_edge(self) -> bool:
        return self.edge_score > 0 and self.trades >= 5

    @property
    def should_trade(self) -> bool:
        """Only trade if we have evidence of positive expectancy."""
        return self.trades < 5 or self.edge_score > -0.2  # allow unproven + positive


class EdgeTracker:
    """
    Tracks per-symbol trading edge from historical data.
    Queries the trades DB and computes rolling edge scores.
    """

    def __init__(self, db_path: str = "swingbot.db", min_trades: int = 5):
        self.db_path = db_path
        self.min_trades = min_trades
        self._cache: Dict[str, SymbolEdge] = {}
        self._cache_time: float = 0

    def refresh(self, lookback_days: int = 30) -> Dict[str, SymbolEdge]:
        """Recompute edge for all symbols from DB."""
        import time

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get all closed trades from last N days
            cursor.execute(f"""
                SELECT symbol, pnl, pnl_percent
                FROM trades
                WHERE pnl IS NOT NULL
                  AND exit_time IS NOT NULL
                  AND exit_time > strftime('%s','now','-{lookback_days} days') * 1000
                ORDER BY exit_time DESC
            """)
            rows = cursor.fetchall()
            conn.close()
        except Exception as e:
            logger.warning(f"[EDGE] DB query failed: {e}")
            return {}

        # Group by symbol
        by_symbol: Dict[str, List[dict]] = {}
        for sym, pnl, pnl_pct in rows:
            if sym not in by_symbol:
                by_symbol[sym] = []
            by_symbol[sym].append({'pnl': pnl or 0, 'pnl_pct': pnl_pct or 0})

        # Compute edge per symbol
        edges: Dict[str, SymbolEdge] = {}
        for sym, trades in by_symbol.items():
            edges[sym] = self._compute_edge(sym, trades)

        self._cache = edges
        self._cache_time = time.time()

        positive = sum(1 for e in edges.values() if e.is_positive_edge)
        logger.info(f"[EDGE] Refreshed — {len(edges)} symbols, {positive} with positive edge")
        return edges

    def _compute_edge(self, symbol: str, trades: List[dict]) -> SymbolEdge:
        """Calculate edge score for a single symbol's trade history."""
        n = len(trades)
        if n == 0:
            return SymbolEdge(symbol, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]

        win_rate = len(wins) / n * 100
        avg_win_pct = sum(t['pnl_pct'] for t in wins) / max(len(wins), 1)
        avg_loss_pct = abs(sum(t['pnl_pct'] for t in losses) / max(len(losses), 1))
        expectancy = sum(t['pnl'] for t in trades) / n

        # Edge score: combination of win rate and reward:risk
        # Normalized to -1..+1
        if avg_loss_pct > 0:
            rr = avg_win_pct / avg_loss_pct
            # Expected value per trade in R units
            ev_r = (win_rate / 100 * rr) - (1 - win_rate / 100)
            # Scale to -1..+1 via tanh
            import math
            edge_score = math.tanh(ev_r)
        else:
            edge_score = 1.0 if wins else 0.0

        # Confidence scales with sample size (capped at 1.0 around 20 trades)
        confidence = min(n / 20.0, 1.0)

        return SymbolEdge(
            symbol=symbol, trades=n, wins=len(wins), losses=len(losses),
            win_rate=round(win_rate, 1),
            avg_win_pct=round(avg_win_pct, 3),
            avg_loss_pct=round(avg_loss_pct, 3),
            expectancy=round(expectancy, 4),
            edge_score=round(edge_score, 3),
            confidence=round(confidence, 2),
        )

    def get_edge(self, symbol: str) -> Optional[SymbolEdge]:
        """Get cached edge for a symbol. Returns None if not in cache."""
        return self._cache.get(symbol)

    def should_trade(self, symbol: str) -> bool:
        """Quick gate — should we trade this symbol based on its edge?"""
        edge = self._cache.get(symbol)
        if edge is None:
            return True  # No history — allow it (new symbol)
        return edge.should_trade

    def get_size_multiplier(self, symbol: str) -> float:
        """
        Returns a size multiplier (0.5-1.5) based on edge.
        Strong positive edge → 1.5x size
        Unproven symbol → 1.0x (no bias)
        Weak/negative edge → 0.5x (still allowed but smaller)
        """
        edge = self._cache.get(symbol)
        if edge is None or edge.trades < self.min_trades:
            return 1.0

        if edge.edge_score >= 0.5:
            return 1.5
        elif edge.edge_score >= 0.1:
            return 1.2
        elif edge.edge_score >= -0.1:
            return 1.0
        elif edge.edge_score >= -0.3:
            return 0.7
        else:
            return 0.5

    def top_symbols(self, n: int = 10) -> List[SymbolEdge]:
        """Return top N symbols by edge score."""
        sorted_edges = sorted(
            [e for e in self._cache.values() if e.trades >= self.min_trades],
            key=lambda e: e.edge_score,
            reverse=True,
        )
        return sorted_edges[:n]

    def print_report(self) -> None:
        """Pretty-print the edge report to console."""
        print(f"\n{'='*65}")
        print(f"  EDGE REPORT — per-symbol historical performance")
        print(f"{'='*65}")
        print(f"  {'Symbol':<15} {'Trades':>7} {'Win%':>6} {'Edge':>7} {'Exp $':>9}")
        print(f"  {'-'*60}")
        for e in sorted(self._cache.values(), key=lambda x: x.edge_score, reverse=True):
            mark = '🔥' if e.edge_score >= 0.3 else '⚠' if e.edge_score < -0.2 else ' '
            print(f"  {e.symbol:<15} {e.trades:>7} {e.win_rate:>5}% "
                  f"{e.edge_score:>+.3f} {e.expectancy:>+8.2f}  {mark}")
        print(f"{'='*65}\n")
