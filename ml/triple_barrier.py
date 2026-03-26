"""
ml/triple_barrier.py -- Triple-Barrier Method labeling.

Implementation of the labeling method from:
"Advances in Financial Machine Learning" by Marcos Lopez de Prado
Chapter 3: Labels

Instead of binary WIN/LOSS, assigns one of three labels:
  +1 = Upper barrier (take profit) touched first
   0 = Vertical barrier (time limit) touched first
  -1 = Lower barrier (stop loss) touched first

This produces significantly richer training data for the
Random Forest model, leading to higher prediction accuracy.
"""

import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BarrierConfig:
    """Configuration for triple-barrier labeling."""
    upper_multiplier: float = 2.0   # TP = entry + (ATR x upper_multiplier)
    lower_multiplier: float = 1.0   # SL = entry - (ATR x lower_multiplier)
    max_holding_hours: int = 48     # Vertical barrier: max trade duration
    min_return_pct: float = 0.001   # Minimum return to consider (filter noise)


@dataclass
class BarrierLabel:
    """Result of triple-barrier labeling for one trade."""
    label: int                      # +1, 0, or -1
    return_pct: float               # Actual return achieved
    hours_to_barrier: float         # How long until a barrier was hit
    barrier_hit: str                # "upper", "lower", or "time"
    upper_barrier: float            # TP price
    lower_barrier: float            # SL price
    entry_price: float
    exit_price: float


class TripleBarrierLabeler:
    """
    Labels historical trades using the Triple-Barrier Method.

    Used in two ways:
    1. Retroactively label completed paper trades for training data
    2. Generate dynamic TP/SL levels based on ATR for new trades

    The key insight from Lopez de Prado:
    "The path matters, not just the destination."
    A trade that slowly drifts to TP is fundamentally different
    from one that rockets there in 2 hours -- and the model
    should learn that distinction.
    """

    def __init__(self, config: Optional[BarrierConfig] = None):
        self.config = config or BarrierConfig()

    def label_trade(
        self,
        entry_price: float,
        candles_after_entry: pd.DataFrame,
        atr_at_entry: float,
        side: str = "BUY"
    ) -> BarrierLabel:
        """
        Label a single trade using triple-barrier method.

        Args:
            entry_price:         Price at which position was opened
            candles_after_entry: OHLCV data from entry to present/close
            atr_at_entry:        ATR value at time of entry (for barrier sizing)
            side:                "BUY" (long) or "SELL" (short)

        Returns:
            BarrierLabel with label (+1, 0, -1) and metadata
        """
        if candles_after_entry.empty or atr_at_entry <= 0:
            return BarrierLabel(
                label=0, return_pct=0, hours_to_barrier=0,
                barrier_hit="time", upper_barrier=entry_price,
                lower_barrier=entry_price, entry_price=entry_price,
                exit_price=entry_price
            )

        # Calculate barrier levels
        upper_dist = atr_at_entry * self.config.upper_multiplier
        lower_dist = atr_at_entry * self.config.lower_multiplier

        if side == "BUY":
            upper_barrier = entry_price + upper_dist   # TP for long
            lower_barrier = entry_price - lower_dist   # SL for long
        else:
            upper_barrier = entry_price - upper_dist   # TP for short
            lower_barrier = entry_price + lower_dist   # SL for short

        # Scan candles for barrier touches
        max_candles = min(
            len(candles_after_entry),
            self.config.max_holding_hours   # Approximate: 1 candle per hour
        )

        for i, (_, candle) in enumerate(candles_after_entry.iloc[:max_candles].iterrows()):
            hours_elapsed = i + 1

            if side == "BUY":
                # Check upper barrier (TP)
                if candle['high'] >= upper_barrier:
                    return_pct = (upper_barrier - entry_price) / entry_price
                    return BarrierLabel(
                        label=+1,
                        return_pct=return_pct,
                        hours_to_barrier=hours_elapsed,
                        barrier_hit="upper",
                        upper_barrier=upper_barrier,
                        lower_barrier=lower_barrier,
                        entry_price=entry_price,
                        exit_price=upper_barrier
                    )

                # Check lower barrier (SL)
                if candle['low'] <= lower_barrier:
                    return_pct = (lower_barrier - entry_price) / entry_price
                    return BarrierLabel(
                        label=-1,
                        return_pct=return_pct,
                        hours_to_barrier=hours_elapsed,
                        barrier_hit="lower",
                        upper_barrier=upper_barrier,
                        lower_barrier=lower_barrier,
                        entry_price=entry_price,
                        exit_price=lower_barrier
                    )

            else:  # SHORT
                if candle['low'] <= upper_barrier:   # TP for short
                    return_pct = (entry_price - upper_barrier) / entry_price
                    return BarrierLabel(
                        label=+1,
                        return_pct=return_pct,
                        hours_to_barrier=hours_elapsed,
                        barrier_hit="upper",
                        upper_barrier=upper_barrier,
                        lower_barrier=lower_barrier,
                        entry_price=entry_price,
                        exit_price=upper_barrier
                    )

                if candle['high'] >= lower_barrier:  # SL for short
                    return_pct = (entry_price - lower_barrier) / entry_price
                    return BarrierLabel(
                        label=-1,
                        return_pct=return_pct,
                        hours_to_barrier=hours_elapsed,
                        barrier_hit="lower",
                        upper_barrier=upper_barrier,
                        lower_barrier=lower_barrier,
                        entry_price=entry_price,
                        exit_price=lower_barrier
                    )

        # Time barrier hit -- neither TP nor SL touched
        last_idx = min(max_candles - 1, len(candles_after_entry) - 1)
        last_price = candles_after_entry.iloc[last_idx]['close']
        return_pct = (last_price - entry_price) / entry_price if side == "BUY" \
                     else (entry_price - last_price) / entry_price

        return BarrierLabel(
            label=0,
            return_pct=return_pct,
            hours_to_barrier=max_candles,
            barrier_hit="time",
            upper_barrier=upper_barrier,
            lower_barrier=lower_barrier,
            entry_price=entry_price,
            exit_price=last_price
        )

    def label_dataset(
        self,
        df: pd.DataFrame,
        atr_column: str = 'atr'
    ) -> pd.Series:
        """
        Label an entire historical dataset.
        Used for backtesting and training data generation.

        For each row in df (representing a potential entry point),
        looks forward in time and applies triple-barrier labeling.

        Returns: Series of labels (+1, 0, -1) indexed like df.
        """
        labels = []

        for i in range(len(df) - self.config.max_holding_hours):
            entry_price = df.iloc[i]['close']
            atr = df.iloc[i].get(atr_column, df.iloc[i]['close'] * 0.02)
            future_candles = df.iloc[i + 1:i + 1 + self.config.max_holding_hours]

            result = self.label_trade(
                entry_price=entry_price,
                candles_after_entry=future_candles,
                atr_at_entry=atr
            )
            labels.append(result.label)

        # Pad the end with NaN (no future data available)
        labels.extend([np.nan] * self.config.max_holding_hours)

        return pd.Series(labels, index=df.index, name='triple_barrier_label')

    def get_dynamic_barriers(
        self,
        entry_price: float,
        atr: float,
        side: str = "BUY"
    ) -> dict:
        """
        Calculate dynamic TP/SL levels using ATR-based barriers.
        Use this instead of fixed multipliers for new trade entries.

        Returns dict with upper, lower barrier prices and
        recommended holding period.
        """
        upper_dist = atr * self.config.upper_multiplier
        lower_dist = atr * self.config.lower_multiplier

        if side == "BUY":
            return {
                'take_profit':    round(entry_price + upper_dist, 8),
                'stop_loss':      round(entry_price - lower_dist, 8),
                'max_hold_hours': self.config.max_holding_hours,
                'rr_ratio':       upper_dist / lower_dist if lower_dist > 0 else 0
            }
        else:
            return {
                'take_profit':    round(entry_price - upper_dist, 8),
                'stop_loss':      round(entry_price + lower_dist, 8),
                'max_hold_hours': self.config.max_holding_hours,
                'rr_ratio':       upper_dist / lower_dist if lower_dist > 0 else 0
            }
