"""
Market Regime Detection for Swingbot.

Detects regimes based on ADX and price vs the slow EMA. The threshold is read
from REGIME_ADX_THRESHOLD or config.yaml when callers do not provide it.
"""
from enum import Enum
import logging
import os
from pathlib import Path
import pandas as pd
import yaml

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    TRANSITION = "TRANSITION"


class RegimeDetector:
    @staticmethod
    def configured_adx_threshold(default: float = 20.0) -> float:
        """Load the ADX threshold from the environment or repository config."""
        raw = os.environ.get("REGIME_ADX_THRESHOLD")
        if raw is not None:
            try:
                return float(raw)
            except ValueError:
                logger.warning("Invalid REGIME_ADX_THRESHOLD=%r; using config", raw)
        try:
            config_path = Path(__file__).resolve().parents[1] / "config.yaml"
            with config_path.open("r", encoding="utf-8") as handle:
                value = yaml.safe_load(handle).get("regime_adx_threshold")
            return float(value) if value is not None else default
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            logger.warning("Could not load regime_adx_threshold: %s", exc)
            return default

    @staticmethod
    def detect(df_row: pd.Series, adx_threshold: float | None = None) -> MarketRegime:
        """Detect the market regime using the configured ADX threshold."""
        threshold = (RegimeDetector.configured_adx_threshold()
                     if adx_threshold is None else float(adx_threshold))
        adx = df_row.get("adx", 0)
        close = df_row.get("close", 0)
        ema_slow = df_row.get("ema_slow", 0)
        if pd.isna(adx):
            adx = 0
        if pd.isna(close) or pd.isna(ema_slow):
            return MarketRegime.RANGING
        if adx >= threshold:
            return (MarketRegime.TRENDING_UP if close > ema_slow
                    else MarketRegime.TRENDING_DOWN)
        return MarketRegime.RANGING
