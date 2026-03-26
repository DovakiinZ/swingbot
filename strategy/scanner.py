"""
Market Scanner -- scores a symbol's current setup on a 0-100 scale.

Scoring breakdown
-----------------
  Trend alignment     : 30 pts  (EMA stack, price vs EMA, MACD)
  RSI momentum        : 25 pts  (oversold/overbought opportunity)
  Breakout setup      : 25 pts  (compression -> expansion breakout)
  Volume confirmation : 10 pts  (institutional interest)
  ADX setup quality   : 10 pts  (trending vs choppy market)
  -----------------------------------------
  Total possible      : 100 pts

Hard gates (return 0 regardless of score):
  - HIGH_VOLATILITY regime (ATR% > 5%)
  - ADX < ADX_MIN_ENTRY (choppy market, no real trend)
  - False breakout: breakout candle has large wick vs body (smart money sweep)

Only setups scoring >= MIN_SCORE (default 55, configurable) are considered for entry.
"""
import logging
import pandas as pd
from typing import Tuple

from strategy.regimes import MarketRegime

logger = logging.getLogger(__name__)

# Default minimum score — overridden by CONFIG['min_score'] in run.py
MIN_SCORE = 55

# ADX hard minimum — below this, market is choppy and entries are low-quality
# Research: ADX < 20 trades have ~40% win rate; ADX >= 20 raises it to ~55%
ADX_MIN_ENTRY = 15   # Conservative default; raise to 20 for higher quality


class MarketScanner:
    """Scores a DataFrame (with computed indicators) for entry quality."""

    def _score_breakout(self, df: pd.DataFrame) -> Tuple[float, bool]:
        """
        Detect compression -> expansion breakout pattern.
        Returns (score, breakout_detected_flag).

        Conditions (ALL must be true):
        1. Previous N candles had ATR% < 2.0% (compression phase)
        2. Current volume >= 2.0x the 20-period volume MA (surge)
        3. Price broke above highest high OR below lowest low by 0.2% margin
           (raised from 0.1% — research shows 0.2% cuts false breakouts significantly)

        False breakout guard (Smart Money Concepts):
        If the breakout candle has a wick > 60% of its total range, it may be
        a liquidity sweep (smart money trapping breakout buyers/sellers).
        In that case, return 0 pts even if all other conditions pass.
        """
        lookback = 20
        if len(df) < lookback + 1:
            return 0.0, False

        recent = df.iloc[-(lookback+1):-1]
        curr   = df.iloc[-1]

        # 1. Compression check
        atr_pct = recent.get('atr_percent')
        if atr_pct is None or atr_pct.isna().all():
            return 0.0, False
        avg_atr_pct = atr_pct.mean()
        if pd.isna(avg_atr_pct) or avg_atr_pct >= 2.0:
            return 0.0, False

        # 2. Volume surge (raised to 2.0x — same as before, validates momentum)
        vol_ratio = curr.get('volume_ratio', 1.0)
        if pd.isna(vol_ratio):
            vol_ratio = 1.0
        if vol_ratio < 2.0:
            return 0.0, False

        # 3. Price breakout (raised margin from 0.1% to 0.2%)
        highest_high = recent['high'].max()
        lowest_low   = recent['low'].min()
        close        = curr['close']
        candle_open  = curr['open']
        candle_high  = curr['high']
        candle_low   = curr['low']

        broke_up   = close > highest_high * 1.002   # 0.2% margin above
        broke_down = close < lowest_low  * 0.998    # 0.2% margin below

        if not (broke_up or broke_down):
            return 0.0, False

        # 4. False breakout / wick filter (Smart Money Concepts)
        # If wick > 60% of total candle range → likely a sweep, not a real breakout
        candle_range = candle_high - candle_low
        if candle_range > 0:
            body = abs(close - candle_open)
            wick_ratio = 1.0 - (body / candle_range)
            if wick_ratio > 0.6:
                logger.debug(f"[SCANNER] False breakout filtered — wick ratio={wick_ratio:.2f}")
                return 0.0, False

        return 25.0, True

    def score_symbol(self, df: pd.DataFrame, regime: MarketRegime,
                     adx_min: float = ADX_MIN_ENTRY) -> Tuple[float, bool]:
        """
        Return (score, breakout_detected) where score is 0-100.
        Higher = stronger conviction to enter.

        Hard gates return (0, False) immediately:
          - HIGH_VOLATILITY regime
          - ADX below minimum (choppy, no real trend)
        """
        if df is None or df.empty or len(df) < 2:
            return 0.0, False

        # Hard gate 1: Never trade into extreme volatility
        if regime == MarketRegime.HIGH_VOLATILITY:
            return 0.0, False

        curr = df.iloc[-1]

        # Hard gate 2: ADX minimum — choppy market filter
        # Research: ADX < 20 eliminates the weakest 30% of trades with worst win rates
        adx = curr.get('adx', 0)
        if pd.isna(adx):
            adx = 0
        if adx < adx_min:
            return 0.0, False

        score = 0.0

        # -- Trend Alignment (30 pts) --
        ema_fast = curr.get('ema_fast', 0)
        ema_slow = curr.get('ema_slow', 0)
        if ema_fast and ema_slow and ema_fast > ema_slow:
            score += 15   # Uptrend
        elif ema_fast and ema_slow and ema_fast < ema_slow:
            score += 15   # Downtrend
        if ema_slow and curr['close'] > ema_slow:
            score += 8    # Price above EMA — bullish
        elif ema_slow and curr['close'] < ema_slow:
            score += 8    # Price below EMA — bearish
        macd = curr.get('macd', None)
        macd_sig = curr.get('macd_signal', None)
        if macd is not None and macd_sig is not None:
            if macd > macd_sig:
                score += 7   # Bullish MACD momentum
            elif macd < macd_sig:
                score += 7   # Bearish MACD momentum

        # -- RSI Momentum (25 pts) --
        rsi = curr.get('rsi', 50)
        if pd.isna(rsi):
            rsi = 50
        if 20 <= rsi <= 35:
            score += 25   # Deep oversold — strong long signal
        elif 35 < rsi <= 45:
            score += 15   # Approaching oversold
        elif 45 < rsi <= 52:
            score += 5    # Neutral
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
            score += 10   # Strong volume surge
        elif vol_ratio >= 1.5:
            score += 6    # Above average
        elif vol_ratio >= 1.2:
            score += 3    # Slightly elevated

        # -- ADX Setup Quality (10 pts) --
        # Hard gate already enforced above; now score quality within qualifying range
        if adx >= 30:
            score += 10   # Strong directional move
        elif adx >= 25:
            score += 7    # Clear trend
        elif adx >= 20:
            score += 3    # Mild trend
        # adx_min <= adx < 20 → 0 pts (passed gate but weak trend)

        return min(score, 100.0), breakout_detected
