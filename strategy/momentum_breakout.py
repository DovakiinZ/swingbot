"""
strategy/momentum_breakout.py — Fast momentum breakout strategy.

Inspired by latency arbitrage principles:
- Detects strong price moves with volume confirmation
- Enters IMMEDIATELY without waiting for RSI/EMA confirmation
- Uses tight ATR-based stop loss
- Designed for short-duration trades (minutes to hours)

When to use:
  RSI+EMA strategy → patient swing trades (hours to days)
  Momentum strategy → fast breakout trades (minutes to hours)
  Both run simultaneously — different timeframes, different signals.
"""

import logging
import time
from typing import Optional
from dataclasses import dataclass
from core.types import Signal, Side, Reason

logger = logging.getLogger(__name__)


@dataclass
class MomentumSignal:
    """Raw momentum event from WebSocket before conversion to Signal."""
    symbol:          str
    direction:       str        # "BUY" or "SELL"
    price:           float
    price_change_pct: float     # % change that triggered signal
    volume_ratio:    float      # how much above average volume
    timestamp:       float
    confidence:      float = 0.0  # 0-1 calculated from strength


class MomentumBreakoutStrategy:
    """
    Fast momentum detection strategy.
    Converts WebSocket momentum events into tradeable Signals.
    """

    def __init__(
        self,
        min_price_change: float = 0.003,   # 0.3% minimum move
        min_volume_ratio: float = 2.0,     # 2x average volume
        sl_atr_mult:      float = 1.5,     # Tight stop — 1.5x ATR
        tp_atr_mult:      float = 2.5,     # Target — 2.5x ATR (R:R = 1.67)
        max_atr_pct:      float = 4.0,     # Skip if too volatile
        cooldown_seconds: int   = 300,     # 5 min cooldown per symbol
    ):
        self.min_price_change = min_price_change
        self.min_volume_ratio = min_volume_ratio
        self.sl_atr_mult      = sl_atr_mult
        self.tp_atr_mult      = tp_atr_mult
        self.max_atr_pct      = max_atr_pct
        self.cooldown_seconds = cooldown_seconds
        self._last_signal: dict = {}   # symbol → timestamp

    def process_momentum_event(
        self,
        symbol:          str,
        direction:       str,
        price:           float,
        price_change_pct: float,
        volume_ratio:    float,
        atr:             float,
        atr_pct:         float,
        timestamp:       float,
        allow_short:     bool = True
    ) -> Optional[Signal]:
        """
        Convert a momentum event into a Signal.
        Returns Signal if all conditions pass, None otherwise.
        """
        # Skip if short not allowed
        if direction == "SELL" and not allow_short:
            return None

        # Cooldown check — don't spam signals for same symbol
        last = self._last_signal.get(symbol, 0)
        if timestamp - last < self.cooldown_seconds:
            logger.debug(f"[MOMENTUM] {symbol} in cooldown — skipping")
            return None

        # Volatility check — skip if too chaotic
        if atr_pct > self.max_atr_pct:
            logger.debug(f"[MOMENTUM] {symbol} too volatile (ATR%={atr_pct:.1f}) — skipping")
            return None

        # Strength check
        abs_change = abs(price_change_pct)
        if abs_change < self.min_price_change:
            return None
        if volume_ratio < self.min_volume_ratio:
            return None

        # Calculate confidence (0-1)
        change_score  = min(abs_change / 0.01, 1.0)    # Max at 1% move
        volume_score  = min(volume_ratio / 4.0, 1.0)   # Max at 4x volume
        confidence    = (change_score * 0.6 + volume_score * 0.4)

        # Build signal with ATR-based stops
        sl_dist = atr * self.sl_atr_mult
        tp_dist = atr * self.tp_atr_mult

        if direction == "BUY":
            stop_loss   = price - sl_dist
            take_profit = price + tp_dist
            side        = Side.BUY
        else:
            stop_loss   = price + sl_dist
            take_profit = price - tp_dist
            side        = Side.SELL

        # Record signal time for cooldown
        self._last_signal[symbol] = timestamp

        logger.warning(
            f"[MOMENTUM SIGNAL] {symbol} {direction} | "
            f"price={price:.4f} | change={price_change_pct:+.3%} | "
            f"vol={volume_ratio:.1f}x | confidence={confidence:.0%}"
        )

        return Signal(
            symbol=symbol,
            side=side,
            reason=Reason.SIGNAL_ENTRY,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=confidence,
        )

    def calculate_confidence_score(
        self,
        price_change_pct: float,
        volume_ratio: float
    ) -> float:
        """Convert momentum metrics to 0-100 scanner score equivalent."""
        change_score = min(abs(price_change_pct) / 0.01 * 40, 40)
        volume_score = min((volume_ratio - 1) / 3 * 35, 35)
        base_score   = 25   # Base for passing all filters
        return min(base_score + change_score + volume_score, 100)
