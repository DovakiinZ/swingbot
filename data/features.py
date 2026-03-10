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
