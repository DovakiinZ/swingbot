from typing import Optional
import pandas as pd
from core.types import Signal, Side, Reason, StrategyParams
from strategy.regimes import MarketRegime

class RsiEmaStrategy:
    def __init__(self):
        pass

    def check_signal(self, 
                     df: pd.DataFrame, 
                     regime: MarketRegime, 
                     params: StrategyParams,
                     current_position: bool = False) -> Optional[Signal]:
        
        if df.empty:
            return None
            
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Safe access
        rsi = curr['rsi']
        ema_fast = curr['ema_fast']
        ema_slow = curr['ema_slow']
        close = curr['close']
        atr = curr['atr']
        atr_pct = (atr / close) * 100
        
        # 1. Safety Checks first
        if regime == MarketRegime.HIGH_VOLATILITY:
            return None # Or reduce size, but strict safety says NO TRADE
            
        # 2. Exit Logic (if we have a position, caller handles this usually, but strategy can signal exit)
        # We return Signal with side=SELL/BUY inverse to open if exit condition met?
        # Typically the main loop checks stops, but strategy checks technical exits.
        
        # 3. Entry Logic (Long Only for now)
        if not current_position:
            # Trend Filter
            trend_ok = ema_fast > ema_slow
            
            # Entry Trigger
            rsi_ok = rsi < params.rsi_entry
            
            # Volatility Cap (Redundant if regime check covers it, but good to have explicit param)
            vol_ok = atr_pct < 5.0 # hardcoded cap or from config
            
            if trend_ok and rsi_ok and vol_ok:
                sl_dist = atr * params.sl_mult
                tp_dist = atr * params.tp_mult
                
                stop_loss = close - sl_dist
                take_profit = close + tp_dist
                
                return Signal(
                    symbol="BTC/USDT", # passed in context ideally
                    side=Side.BUY,
                    reason=Reason.SIGNAL_ENTRY,
                    price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    params=params
                )
                
        else:
            # Exit Logic for existing Long
            if rsi > params.rsi_exit:
                return Signal(
                    symbol="BTC/USDT",
                    side=Side.SELL,
                    reason=Reason.RSI_EXIT, # Technical exit
                    price=close,
                    stop_loss=0,
                    take_profit=0
                )
            
            # Trend Flip Exit
            if ema_fast < ema_slow:
                 return Signal(
                    symbol="BTC/USDT",
                    side=Side.SELL,
                    reason=Reason.TREND_FLIP, 
                    price=close,
                    stop_loss=0,
                    take_profit=0
                )
                
        return None
