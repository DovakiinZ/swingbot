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
                     current_position: bool = False,
                     symbol: str = "BTC/USDT") -> Optional[Signal]:

        if df.empty:
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        rsi      = curr['rsi']
        ema_fast = curr['ema_fast']
        ema_slow = curr['ema_slow']
        close    = curr['close']
        atr      = curr['atr']
        atr_pct  = (atr / close) * 100

        # Never trade in extreme volatility
        if regime == MarketRegime.HIGH_VOLATILITY:
            return None

        if not current_position:
            # ── Entry Logic (Long Only) ───────────────────────────────────────
            trend_ok = ema_fast > ema_slow          # Uptrend
            rsi_ok   = rsi < params.rsi_entry       # Oversold
            vol_ok   = atr_pct < 5.0                # Not too wild

            if trend_ok and rsi_ok and vol_ok:
                sl_dist    = atr * params.sl_mult
                tp_dist    = atr * params.tp_mult
                stop_loss  = close - sl_dist
                take_profit = close + tp_dist

                return Signal(
                    symbol=symbol,
                    side=Side.BUY,
                    reason=Reason.SIGNAL_ENTRY,
                    price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    params=params
                )

        else:
            # ── Exit Logic for existing Long ──────────────────────────────────
            if rsi > params.rsi_exit:
                return Signal(
                    symbol=symbol,
                    side=Side.SELL,
                    reason=Reason.RSI_EXIT,
                    price=close,
                    stop_loss=0,
                    take_profit=0
                )

            # Trend flip — momentum gone
            if ema_fast < ema_slow:
                return Signal(
                    symbol=symbol,
                    side=Side.SELL,
                    reason=Reason.TREND_FLIP,
                    price=close,
                    stop_loss=0,
                    take_profit=0
                )

        return None
