from typing import Optional
import pandas as pd
from core.types import Signal, Side, Reason, StrategyParams, Position
from strategy.regimes import MarketRegime


class RsiEmaStrategy:
    def __init__(self):
        pass

    def check_signal(self,
                     df: pd.DataFrame,
                     regime: MarketRegime,
                     params: StrategyParams,
                     current_position=None,
                     symbol: str = "BTC/USDT",
                     allow_short: bool = True) -> Optional[Signal]:
        """
        Generate entry/exit signals for both long and short positions.

        current_position: None/False for no position, True for backward compat
                          (assumes long), or a Position object.
        """
        if df.empty:
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        rsi = curr['rsi']
        ema_fast = curr['ema_fast']
        ema_slow = curr['ema_slow']
        close = curr['close']
        atr = curr['atr']
        atr_pct = (atr / close) * 100

        # Never trade in extreme volatility
        if regime == MarketRegime.HIGH_VOLATILITY:
            return None

        # Determine current position state
        has_position = False
        current_side = None
        if isinstance(current_position, Position):
            has_position = True
            current_side = current_position.side
        elif current_position is True:
            has_position = True
            current_side = Side.BUY  # backward compat assumes long

        if not has_position:
            # ── Long Entry (bullish trend + oversold) ────────────────────────
            trend_ok = ema_fast > ema_slow
            rsi_ok = rsi < params.rsi_entry
            vol_ok = atr_pct < 5.0

            if trend_ok and rsi_ok and vol_ok:
                sl_dist = atr * params.sl_mult
                tp_dist = atr * params.tp_mult
                stop_loss = close - sl_dist
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

            # ── Short Entry (bearish trend + overbought) ────────────────────
            if allow_short:
                short_trend_ok = ema_fast < ema_slow          # Confirmed downtrend
                short_rsi_ok   = rsi > params.rsi_exit        # Overbought
                short_vol_ok   = atr_pct < 5.0                # Not chaotic

                if short_trend_ok and short_rsi_ok and short_vol_ok:
                    return Signal(
                        symbol=symbol,
                        side=Side.SELL,
                        reason=Reason.SIGNAL_ENTRY,
                        price=close,
                        stop_loss=close + (atr * params.sl_mult),    # SL above price
                        take_profit=close - (atr * params.tp_mult),  # TP below price
                        params=params
                    )

        else:
            # ── Exit Logic for existing Long ────────────────────────────────
            if current_side == Side.BUY:
                if rsi > params.rsi_exit:
                    return Signal(
                        symbol=symbol,
                        side=Side.SELL,
                        reason=Reason.RSI_EXIT,
                        price=close,
                        stop_loss=0,
                        take_profit=0
                    )
                if ema_fast < ema_slow:
                    return Signal(
                        symbol=symbol,
                        side=Side.SELL,
                        reason=Reason.TREND_FLIP,
                        price=close,
                        stop_loss=0,
                        take_profit=0
                    )

            # ── Short Exit -- cover when oversold or trend flips ────────────
            elif current_side == Side.SELL:
                if rsi < params.rsi_entry:
                    return Signal(
                        symbol=symbol,
                        side=Side.BUY,
                        reason=Reason.RSI_EXIT,
                        price=close,
                        stop_loss=0,
                        take_profit=0
                    )
                if ema_fast > ema_slow:
                    return Signal(
                        symbol=symbol,
                        side=Side.BUY,
                        reason=Reason.TREND_FLIP,
                        price=close,
                        stop_loss=0,
                        take_profit=0
                    )

        return None
