"""
Market Scanner -- scores a symbol's current setup on a 0-100 scale.

Scoring breakdown
-----------------
  Trend alignment     : 30 pts  (EMA stack, price vs EMA, MACD)
  RSI momentum        : 25 pts  (oversold bounce opportunity)
  Breakout setup      : 25 pts  (compression -> expansion breakout)
  Volume confirmation : 10 pts  (institutional interest)
  ADX setup quality   : 10 pts  (trending vs choppy market)
  -----------------------------------------
  Total possible      : 100 pts

Only setups scoring >= MIN_SCORE (default 65) are considered for entry.
HIGH_VOLATILITY regime always returns 0 -- no trades in chaos.
"""
import logging
import pandas as pd
from typing import Optional, Tuple

from strategy.regimes import MarketRegime

logger = logging.getLogger(__name__)

# Minimum score to open a new position
MIN_SCORE = 65


class MarketScanner:
    """Scores a DataFrame (with computed indicators) for entry quality."""

    def _score_breakout(self, df: pd.DataFrame) -> Tuple[float, bool]:
        """
        Detect compression -> expansion breakout pattern.
        Returns (score, breakout_detected_flag).

        Breakout conditions (ALL must be true):
        1. Previous N candles had ATR% < 2.0% (compression phase)
        2. Current volume >= 2.0x the 20-period volume MA (surge)
        3. Price broke above highest high OR below lowest low of last N candles
           by at least 0.1% margin

        Returns 25 pts and True if breakout detected, else 0 pts and False.
        """
        lookback = 20
        if len(df) < lookback + 1:
            return 0.0, False

        recent = df.iloc[-(lookback+1):-1]  # Previous N candles
        curr   = df.iloc[-1]

        # 1. Compression check
        atr_pct = recent.get('atr_percent')
        if atr_pct is None or atr_pct.isna().all():
            return 0.0, False
        avg_atr_pct = atr_pct.mean()
        if pd.isna(avg_atr_pct) or avg_atr_pct >= 2.0:
            return 0.0, False

        # 2. Volume surge
        vol_ratio = curr.get('volume_ratio', 1.0)
        if pd.isna(vol_ratio):
            vol_ratio = 1.0
        if vol_ratio < 2.0:
            return 0.0, False

        # 3. Price breakout
        highest_high = recent['high'].max()
        lowest_low   = recent['low'].min()
        close        = curr['close']

        broke_up   = close > highest_high * 1.001   # 0.1% margin above
        broke_down = close < lowest_low  * 0.999    # 0.1% margin below

        if broke_up or broke_down:
            return 25.0, True

        return 0.0, False

    def score_symbol(self, df: pd.DataFrame, regime: MarketRegime) -> Tuple[float, bool]:
        """
        Return (score, breakout_detected) where score is 0-100 representing
        setup quality. Higher = stronger conviction to enter.
        """
        if df is None or df.empty or len(df) < 2:
            return 0.0, False

        # Never trade into extreme volatility
        if regime == MarketRegime.HIGH_VOLATILITY:
            return 0.0, False

        curr = df.iloc[-1]
        score = 0.0

        # -- Trend Alignment (30 pts) --
        ema_fast = curr.get('ema_fast', 0)
        ema_slow = curr.get('ema_slow', 0)
        # Award 15 pts for a clear trend in either direction
        if ema_fast and ema_slow and ema_fast > ema_slow:
            score += 15   # Uptrend (long bias)
        elif ema_fast and ema_slow and ema_fast < ema_slow:
            score += 15   # Downtrend (short bias)
        # Price vs EMA slow: award for alignment in either direction
        if ema_slow and curr['close'] > ema_slow:
            score += 8    # Price above — bullish
        elif ema_slow and curr['close'] < ema_slow:
            score += 8    # Price below — bearish
        macd = curr.get('macd', None)
        macd_sig = curr.get('macd_signal', None)
        if macd is not None and macd_sig is not None:
            if macd > macd_sig:
                score += 7   # Bullish momentum
            elif macd < macd_sig:
                score += 7   # Bearish momentum

        # -- RSI Momentum (25 pts) --
        rsi = curr.get('rsi', 50)
        if pd.isna(rsi):
            rsi = 50
        # Long setups (oversold)
        if 20 <= rsi <= 35:
            score += 25   # Deep oversold — strong long signal
        elif 35 < rsi <= 45:
            score += 15   # Approaching oversold
        elif 45 < rsi <= 52:
            score += 5    # Neutral
        # Short setups (overbought)
        elif 65 <= rsi <= 80:
            score += 15   # Approaching overbought — short signal
        elif rsi > 80:
            score += 25   # Deep overbought — strong short signal

        # -- Breakout Setup (25 pts) --
        breakout_score, breakout_detected = self._score_breakout(df)
        score += breakout_score

        # -- Volume Confirmation (10 pts) --
        vol_ratio = curr.get('volume_ratio', 1.0)
        if pd.isna(vol_ratio):
            vol_ratio = 1.0
        if vol_ratio >= 2.0:
            score += 10   # Volume surge
        elif vol_ratio >= 1.5:
            score += 6    # Above average
        elif vol_ratio >= 1.2:
            score += 3    # Slightly elevated

        # -- ADX Setup Quality (10 pts) --
        adx = curr.get('adx', 0)
        if pd.isna(adx):
            adx = 0
        if adx >= 30:
            score += 10   # Strong directional move
        elif adx >= 25:
            score += 7    # Clear trend
        elif adx >= 20:
            score += 3    # Mild trend

        return min(score, 100.0), breakout_detected
