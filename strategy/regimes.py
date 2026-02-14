from enum import Enum
import pandas as pd

class MarketRegime(Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNCERTAIN = "UNCERTAIN"

class RegimeDetector:
    @staticmethod
    def detect(df_row: pd.Series, volatility_cap: float = 5.0) -> MarketRegime:
        atr_pct = df_row.get('atr_percent', 0)
        adx = df_row.get('adx', 0)
        
        if atr_pct > volatility_cap:
            return MarketRegime.HIGH_VOLATILITY
        
        if adx > 25:
            return MarketRegime.TRENDING
        elif adx < 20:
            return MarketRegime.RANGING
            
        return MarketRegime.UNCERTAIN
