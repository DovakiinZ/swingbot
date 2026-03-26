import pandas as pd
import ta
from typing import List, Dict
from core.types import Candle

class FeatureEngine:
    @staticmethod
    def compute_indicators(candles: List[Candle]) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame([vars(c) for c in candles])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        # Trend
        df['ema_fast'] = ta.trend.EMAIndicator(close=df['close'], window=20).ema_indicator()
        df['ema_slow'] = ta.trend.EMAIndicator(close=df['close'], window=50).ema_indicator()

        # Momentum
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['rsi_7'] = ta.momentum.RSIIndicator(close=df['close'], window=7).rsi()

        # MACD (12/26/9)
        macd_ind = ta.trend.MACD(close=df['close'], window_fast=12, window_slow=26, window_sign=9)
        df['macd'] = macd_ind.macd()
        df['macd_signal'] = macd_ind.macd_signal()
        df['macd_hist'] = macd_ind.macd_diff()

        # Volatility
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        df['atr_percent'] = (df['atr'] / df['close']) * 100

        # Bollinger Bands (20, 2 std)
        bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_lower'] = bb.bollinger_lband()
        df['bb_mid']   = bb.bollinger_mavg()

        # Volume MA + ratio (how much above/below average)
        df['volume_ma'] = df['volume'].rolling(window=20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma'].replace(0, 1)

        # ADX for regime
        adx = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        df['adx'] = adx.adx()

        return df

    @staticmethod
    def compute_dynamic_features(candles: List[Candle], params: Dict[str, int]) -> pd.DataFrame:
        """
        Compute features based on dynamic params from the bandit arm.
        """
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame([vars(c) for c in candles])

        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=params.get('rsi_period', 14)).rsi()
        df['ema_fast'] = ta.trend.EMAIndicator(close=df['close'], window=params.get('ema_fast', 20)).ema_indicator()
        df['ema_slow'] = ta.trend.EMAIndicator(close=df['close'], window=params.get('ema_slow', 50)).ema_indicator()

        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=params.get('atr_period', 14)).average_true_range()

        return df

    @staticmethod
    def extract_ml_features(df: pd.DataFrame,
                             scanner_score: float = 0,
                             breakout_detected: bool = False,
                             macro_scale: float = 1.0,
                             fear_greed: float = 50.0) -> dict:
        """
        Extract the full feature vector for ML inference.
        Returns a flat dict matching the trade_features schema.
        All features are normalized/cleaned (no NaN, no inf).
        """
        curr = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else curr

        def safe(val, default=0.0):
            import math
            if val is None:
                return default
            try:
                v = float(val)
                if math.isnan(v) or not math.isfinite(v):
                    return default
                return v
            except (TypeError, ValueError):
                return default

        close    = safe(curr['close'])
        bb_upper = safe(curr.get('bb_upper', 0))
        bb_lower = safe(curr.get('bb_lower', 0))
        bb_mid   = safe(curr.get('bb_mid', close))
        bb_range = (bb_upper - bb_lower) if bb_upper > bb_lower else 1

        ema_fast_curr = safe(curr.get('ema_fast', close))
        ema_fast_prev = safe(prev.get('ema_fast', ema_fast_curr))
        ema_slow_curr = safe(curr.get('ema_slow', close))
        ema_slow_prev = safe(prev.get('ema_slow', ema_slow_curr))

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        return {
            'price':           close,
            'rsi_14':          safe(curr.get('rsi', 50)),
            'rsi_7':           safe(curr.get('rsi_7', 50)),
            'macd':            safe(curr.get('macd', 0)),
            'macd_signal':     safe(curr.get('macd_signal', 0)),
            'macd_hist':       safe(curr.get('macd_hist', 0)),
            'ema_fast':        ema_fast_curr,
            'ema_slow':        ema_slow_curr,
            'ema_fast_slope':  (ema_fast_curr - ema_fast_prev) / max(ema_fast_prev, 1),
            'ema_slow_slope':  (ema_slow_curr - ema_slow_prev) / max(ema_slow_prev, 1),
            'adx':             safe(curr.get('adx', 0)),
            'atr':             safe(curr.get('atr', 0)),
            'atr_percent':     safe(curr.get('atr_percent', 0)),
            'bb_position':     (close - bb_lower) / bb_range,
            'bb_width':        bb_range / max(bb_mid, 1),
            'volume_ratio':    safe(curr.get('volume_ratio', 1)),
            'scanner_score':   scanner_score,
            'breakout_detected': 1 if breakout_detected else 0,
            'macro_scale':     macro_scale,
            'fear_greed':      fear_greed,
            'hour_of_day':     now.hour,
            'day_of_week':     now.weekday(),
        }
