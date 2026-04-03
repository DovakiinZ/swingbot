"""
Signal Confidence Scorer for Swingbot.

Scores each trade setup from 0-100 based on 5 weighted conditions:
  RSI oversold (<40)          : +20 pts
  Volume surge (>1.5x avg)    : +20 pts
  3 consecutive bullish candles: +25 pts
  Price near support           : +20 pts
  Regime is TRENDING_UP        : +15 pts
                         Total : 100 pts max

Only allow entry if score >= THRESHOLD (default 70).
Logs the full breakdown for every signal evaluated.
"""
import logging
import pandas as pd
from typing import Dict

from strategy.regimes import MarketRegime

logger = logging.getLogger(__name__)

THRESHOLD = 70


class SignalScorer:
    """Scores trade setups on a 0-100 scale with transparent breakdown."""

    def __init__(self, threshold: int = THRESHOLD):
        self.threshold = threshold

    def score(self, df: pd.DataFrame, regime: MarketRegime,
              symbol: str = "") -> Dict:
        """
        Calculate signal confidence score with full breakdown.

        Args:
            df: DataFrame with indicators (rsi, volume_ratio, bb_lower, close, etc.)
            regime: Current MarketRegime
            symbol: Symbol name for logging

        Returns:
            dict with keys: total, breakdown, passed
        """
        if df is None or df.empty or len(df) < 5:
            return {"total": 0, "breakdown": {}, "passed": False}

        curr = df.iloc[-1]
        breakdown = {}

        # 1. RSI below 40 (oversold) → +20 pts
        rsi = curr.get('rsi', 50)
        if pd.isna(rsi):
            rsi = 50
        breakdown['rsi_oversold'] = 20 if rsi < 40 else 0

        # 2. Volume above 1.5x 20-period average → +20 pts
        vol_ratio = curr.get('volume_ratio', 1.0)
        if pd.isna(vol_ratio):
            vol_ratio = 1.0
        breakdown['volume_surge'] = 20 if vol_ratio > 1.5 else 0

        # 3. 3 consecutive bullish candles confirming trend → +25 pts
        if len(df) >= 4:
            last3 = df.iloc[-4:-1]  # 3 closed candles before current
            bullish_count = sum(
                1 for i in range(len(last3))
                if last3.iloc[i]['close'] > last3.iloc[i]['open']
            )
            breakdown['bullish_candles'] = 25 if bullish_count >= 3 else 0
        else:
            breakdown['bullish_candles'] = 0

        # 4. Price near support (within 1% of 20-period low) → +20 pts
        close = curr.get('close', 0)
        if len(df) >= 20:
            low_20 = df['low'].iloc[-20:].min()
            if close > 0 and low_20 > 0:
                dist_pct = (close - low_20) / close
                breakdown['near_support'] = 20 if dist_pct <= 0.01 else 0
            else:
                breakdown['near_support'] = 0
        else:
            breakdown['near_support'] = 0

        # 5. Market regime is TRENDING_UP → +15 pts
        breakdown['regime_trending_up'] = 15 if regime == MarketRegime.TRENDING_UP else 0

        total = sum(breakdown.values())
        passed = total >= self.threshold

        # Log the full breakdown
        parts = [f"{k}={v}" for k, v in breakdown.items() if v > 0]
        active = " + ".join(parts) if parts else "none"
        status = "PASS" if passed else "FAIL"
        logger.warning(
            f"[SCORER] {symbol}: {total}/100 ({status}) | {active}"
        )

        return {
            "total": total,
            "breakdown": breakdown,
            "passed": passed,
        }
