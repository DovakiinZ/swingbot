"""
Market Regime Detection for Swingbot.

Detects 3 regimes based on ADX and price vs 50 EMA:
  TRENDING_UP   — ADX >= 20 and price above 50 EMA → allow long entries
  TRENDING_DOWN — ADX >= 20 and price below 50 EMA → allow short entries only
  RANGING       — ADX < 20 → skip all entries (choppy, no edge)
"""
from enum import Enum
import logging
import pandas as pd

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"


class RegimeDetector:
    @staticmethod
    def detect(df_row: pd.Series, adx_threshold: float = 20.0) -> MarketRegime:
        """
        Detect market regime from a single candle row with indicators.

        Args:
            df_row: Series with 'adx', 'close', 'ema_slow' (50-period EMA)
            adx_threshold: ADX level separating trending from ranging (default 20)

        Returns:
            MarketRegime enum value
        """
        adx = df_row.get('adx', 0)
        close = df_row.get('close', 0)
        ema_slow = df_row.get('ema_slow', 0)  # 50-period EMA

        if pd.isna(adx):
            adx = 0
        if pd.isna(close) or pd.isna(ema_slow):
            return MarketRegime.RANGING

        if adx >= adx_threshold:
            if close > ema_slow:
                return MarketRegime.TRENDING_UP
            else:
                return MarketRegime.TRENDING_DOWN

        return MarketRegime.RANGING
