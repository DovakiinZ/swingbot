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
        
        # Volatility
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        df['atr_percent'] = (df['atr'] / df['close']) * 100
        
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
        # Only compute what's needed for the specific strategy params to save time? 
        # Or just re-compute all relevant ones.
        # For simplicity, we might just stick to the main compute_indicators for now
        # and allow strategy logic to request specific periods if optimized.
        
        # Custom calc based on params
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=params.get('rsi_period', 14)).rsi()
        df['ema_fast'] = ta.trend.EMAIndicator(close=df['close'], window=params.get('ema_fast', 20)).ema_indicator()
        df['ema_slow'] = ta.trend.EMAIndicator(close=df['close'], window=params.get('ema_slow', 50)).ema_indicator()
        
        df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=params.get('atr_period', 14)).average_true_range()
        
        return df
