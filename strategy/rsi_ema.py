from typing import Optional
import logging
import pandas as pd
import numpy as np
from core.types import Signal, Side, Reason, StrategyParams, Position
from strategy.regimes import MarketRegime

logger = logging.getLogger(__name__)


class ReversionStrategies:
    """
    Implementation of '20 EMA Reversion' and 'Breakout Consolidation' patterns.
    """

    @staticmethod
    def check_20ema_reversion(df: pd.DataFrame, symbol: str) -> Optional[dict]:
        """
        20 EMA Reversion:
        Price pulls back to 20 EMA in a trending market.
        Conditions for BUY: 
          1. Close > 50 EMA (Bullish context)
          2. Previous low touches or pierces 20 EMA
          3. Current close recovers above 20 EMA
        """
        if len(df) < 21:
            return None
            
        curr = df.iloc[-2]
        prev = df.iloc[-3]
        
        ema20 = curr['ema_fast'] # Assuming ema_fast is 20 in config
        ema50 = curr['ema_slow'] # Assuming ema_slow is 50 in config
        
        # Bullish Reversion
        if curr['close'] > ema50 and prev['low'] <= prev['ema_fast'] and curr['close'] > ema20:
            return {"side": Side.BUY, "reason": "20 EMA Reversion (BULL)"}
            
        # Bearish Reversion
        if curr['close'] < ema50 and prev['high'] >= prev['ema_fast'] and curr['close'] < ema20:
            return {"side": Side.SELL, "reason": "20 EMA Reversion (BEAR)"}
            
        return None

    @staticmethod
    def check_breakout_consolidation(df: pd.DataFrame, symbol: str) -> Optional[dict]:
        """
        Breakout Consolidation:
        Detects price squeezing (low volatility) after a breakout.
        """
        if len(df) < 25:
            return None
            
        # Look for breakout in the last 20 candles
        recent_max = df.iloc[-22:-2]['high'].max()
        if df.iloc[-2]['close'] > recent_max:
            # Check for tight consolidation (ATR compression)
            recent_atr = df.iloc[-6:-1]['atr'].mean()
            prev_atr = df.iloc[-20:-6]['atr'].mean()
            
            if recent_atr < prev_atr * 0.8: # ATR compression
                return {"side": Side.BUY, "reason": "Breakout Consolidation"}
        
        return None


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

    # FIX 3: Minimum ATR multipliers — prevents stops too tight for market noise
    MIN_SL_MULT = 1.5   # SL at least 1.5x ATR away (dynamic ATR-based stop)
    MIN_TP_MULT = 3.0   # TP at least 3.0x ATR away → ensures 2:1 R:R minimum

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
        if df.empty or len(df) < 6:
            return None

        # ── No repainting ────────────────────────────────────────────────────
        curr = df.iloc[-2]
        prev = df.iloc[-3]

        rsi = curr['rsi']
        ema_fast = curr['ema_fast']
        ema_slow = curr['ema_slow']
        close = curr['close']
        atr = curr['atr']
        atr_pct = (atr / close) * 100

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
            # 1. Check New Patterns First
            reversion = ReversionStrategies.check_20ema_reversion(df, symbol)
            if reversion and (reversion['side'] == Side.BUY or allow_short):
                logger.info(f"[STRATEGY] {symbol} {reversion['reason']} detected")
                return self._build_signal(symbol, reversion['side'], reversion['reason'], close, atr, params)

            breakout = ReversionStrategies.check_breakout_consolidation(df, symbol)
            if breakout:
                logger.info(f"[STRATEGY] {symbol} {breakout['reason']} detected")
                return self._build_signal(symbol, breakout['side'], breakout['reason'], close, atr, params)

            # 2. Standard RSI-EMA Strategy
            # Only trade in trending regimes — skip RANGING (no edge in chop)
            if regime == MarketRegime.RANGING:
                logger.info(f"[SKIP] {symbol}: RANGING regime (ADX < 20) — no entries")
                return None

            # FIX 2: Trend confirmation — at least 2 of last 3 closed candles must agree
            last3 = df.iloc[-5:-2]
            bullish_count = sum(1 for i in range(len(last3)) if last3.iloc[i]['close'] > last3.iloc[i]['open'])
            bearish_count = sum(1 for i in range(len(last3)) if last3.iloc[i]['close'] < last3.iloc[i]['open'])
            bullish_confirmed = bullish_count >= 2
            bearish_confirmed = bearish_count >= 2

            # ── Long Entry (bullish trend + oversold) ────────────────────────
            trend_ok = ema_fast > ema_slow
            rsi_ok = rsi < params.rsi_entry
            vol_ok = atr_pct < 5.0

            if trend_ok and rsi_ok and vol_ok:
                if bullish_confirmed:
                    return self._build_signal(symbol, Side.BUY, Reason.SIGNAL_ENTRY, close, atr, params)

            # ── Short Entry (bearish trend + overbought) ────────────────────
            if allow_short:
                short_trend_ok = ema_fast < ema_slow
                short_rsi_ok   = rsi > params.rsi_exit
                short_vol_ok   = atr_pct < 5.0

                if short_trend_ok and short_rsi_ok and short_vol_ok:
                    if bearish_confirmed:
                        return self._build_signal(symbol, Side.SELL, Reason.SIGNAL_ENTRY, close, atr, params)

        else:
            # ── Exit Logic for existing Long ────────────────────────────────
            if current_side == Side.BUY:
                if rsi > params.rsi_exit:
                    return Signal(symbol=symbol, side=Side.SELL, reason=Reason.RSI_EXIT, price=close, stop_loss=0, take_profit=0)
                if ema_fast < ema_slow:
                    return Signal(symbol=symbol, side=Side.SELL, reason=Reason.TREND_FLIP, price=close, stop_loss=0, take_profit=0)

            # ── Short Exit -- cover when oversold or trend flips ────────────
            elif current_side == Side.SELL:
                if rsi < params.rsi_entry:
                    return Signal(symbol=symbol, side=Side.BUY, reason=Reason.RSI_EXIT, price=close, stop_loss=0, take_profit=0)
                if ema_fast > ema_slow:
                    return Signal(symbol=symbol, side=Side.BUY, reason=Reason.TREND_FLIP, price=close, stop_loss=0, take_profit=0)

        return None

    def _build_signal(self, symbol, side, reason, price, atr, params):
        sl_mult = max(params.sl_mult, self.MIN_SL_MULT)
        tp_mult = max(params.tp_mult, self.MIN_TP_MULT)

        if self._tb_labeler:
            barriers = self._tb_labeler.get_dynamic_barriers(entry_price=price, atr=atr, side=side.value)
            sl, tp = barriers['stop_loss'], barriers['take_profit']
        else:
            if side == Side.BUY:
                sl, tp = price - (atr * sl_mult), price + (atr * tp_mult)
            else:
                sl, tp = price + (atr * sl_mult), price - (atr * tp_mult)

        return Signal(
            symbol=symbol, side=side, reason=reason if isinstance(reason, Reason) else Reason.SIGNAL_ENTRY,
            price=price, stop_loss=sl, take_profit=tp, params=params
        )
