from typing import Optional
import logging
import pandas as pd
from core.types import Signal, Side, Reason, StrategyParams, Position
from strategy.regimes import MarketRegime

logger = logging.getLogger(__name__)


class RsiEmaStrategy:
    def __init__(self, tb_config: Optional[dict] = None):
        """
        Args:
            tb_config: Triple-barrier config dict with keys:
                       'enabled', 'upper_multiplier', 'lower_multiplier', 'max_holding_hours'
                       If None or not enabled, uses standard ATR multipliers from params.
        """
        self._tb_labeler = None
        if tb_config and tb_config.get('use_for_signals', False):
            try:
                from ml.triple_barrier import TripleBarrierLabeler, BarrierConfig
                self._tb_labeler = TripleBarrierLabeler(BarrierConfig(
                    upper_multiplier=tb_config.get('upper_multiplier', 2.0),
                    lower_multiplier=tb_config.get('lower_multiplier', 1.0),
                    max_holding_hours=tb_config.get('max_holding_hours', 48)
                ))
                logger.info("[Strategy] Triple-Barrier dynamic TP/SL enabled")
            except Exception as e:
                logger.warning(f"[Strategy] TB init failed, using standard: {e}")

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
                # Use Triple-Barrier dynamic barriers if available
                if self._tb_labeler:
                    barriers = self._tb_labeler.get_dynamic_barriers(
                        entry_price=close, atr=atr, side="BUY"
                    )
                    stop_loss = barriers['stop_loss']
                    take_profit = barriers['take_profit']
                else:
                    stop_loss = close - (atr * params.sl_mult)
                    take_profit = close + (atr * params.tp_mult)

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
                    # Use Triple-Barrier dynamic barriers if available
                    if self._tb_labeler:
                        barriers = self._tb_labeler.get_dynamic_barriers(
                            entry_price=close, atr=atr, side="SELL"
                        )
                        short_sl = barriers['stop_loss']
                        short_tp = barriers['take_profit']
                    else:
                        short_sl = close + (atr * params.sl_mult)
                        short_tp = close - (atr * params.tp_mult)

                    return Signal(
                        symbol=symbol,
                        side=Side.SELL,
                        reason=Reason.SIGNAL_ENTRY,
                        price=close,
                        stop_loss=short_sl,
                        take_profit=short_tp,
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
