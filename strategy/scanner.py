"""
Market Scanner — scores a symbol's current setup on a 0-100 scale.

Scoring breakdown
─────────────────
  Trend alignment     : 30 pts  (EMA stack, price vs EMA, MACD)
  RSI momentum        : 25 pts  (oversold bounce opportunity)
  Volume confirmation : 20 pts  (institutional interest)
  ADX setup quality   : 15 pts  (trending vs choppy market)
  Bollinger position  :  10 pts  (near lower band = mean-reversion entry)
  ─────────────────────────────
  Total possible      : 100 pts

Only setups scoring >= MIN_SCORE (default 55) are considered for entry.
HIGH_VOLATILITY regime always returns 0 — no trades in chaos.
"""
import logging
import pandas as pd
from typing import Optional

from strategy.regimes import MarketRegime

logger = logging.getLogger(__name__)

# Minimum score to open a new position
MIN_SCORE = 55


class MarketScanner:
    """Scores a DataFrame (with computed indicators) for entry quality."""

    def score_symbol(self, df: pd.DataFrame, regime: MarketRegime) -> float:
        """
        Return a float 0–100 representing setup quality.
        Higher = stronger conviction to enter long.
        """
        if df is None or df.empty or len(df) < 2:
            return 0.0

        # Never trade into extreme volatility
        if regime == MarketRegime.HIGH_VOLATILITY:
            return 0.0

        curr = df.iloc[-1]
        score = 0.0

        # ── Trend Alignment (30 pts) ──────────────────────────────────────────
        # EMA fast > slow → uptrend in place
        ema_fast = curr.get('ema_fast', 0)
        ema_slow = curr.get('ema_slow', 0)
        if ema_fast and ema_slow and ema_fast > ema_slow:
            score += 15
        # Price above slow EMA → we are in the trend, not just testing it
        if ema_slow and curr['close'] > ema_slow:
            score += 8
        # MACD bullish (momentum confirming trend)
        macd = curr.get('macd', None)
        macd_sig = curr.get('macd_signal', None)
        if macd is not None and macd_sig is not None and macd > macd_sig:
            score += 7

        # ── RSI Momentum (25 pts) ─────────────────────────────────────────────
        rsi = curr.get('rsi', 50)
        if pd.isna(rsi):
            rsi = 50
        if 20 <= rsi <= 35:
            score += 25   # Deep oversold — prime bounce zone
        elif 35 < rsi <= 45:
            score += 15   # Approaching oversold — good risk/reward
        elif 45 < rsi <= 52:
            score += 5    # Neutral — weak signal only

        # ── Volume Confirmation (20 pts) ─────────────────────────────────────
        vol_ratio = curr.get('volume_ratio', 1.0)
        if pd.isna(vol_ratio):
            vol_ratio = 1.0
        if vol_ratio >= 2.0:
            score += 20   # Volume surge — strong institutional interest
        elif vol_ratio >= 1.5:
            score += 12   # Above average — decent confirmation
        elif vol_ratio >= 1.2:
            score += 6    # Slightly elevated

        # ── ADX Setup Quality (15 pts) ────────────────────────────────────────
        adx = curr.get('adx', 0)
        if pd.isna(adx):
            adx = 0
        if adx >= 30:
            score += 15   # Strong directional move
        elif adx >= 25:
            score += 10   # Clear trend
        elif adx >= 20:
            score += 5    # Mild trend

        # ── Bollinger Band Position (10 pts) ──────────────────────────────────
        bb_upper = curr.get('bb_upper', 0)
        bb_lower = curr.get('bb_lower', 0)
        if bb_upper and bb_lower and bb_upper > bb_lower:
            bb_range = bb_upper - bb_lower
            bb_position = (curr['close'] - bb_lower) / bb_range
            if bb_position <= 0.15:
                score += 10   # Very close to lower band — oversold pressure
            elif bb_position <= 0.30:
                score += 5    # Near lower band — still favourable

        return min(score, 100.0)
