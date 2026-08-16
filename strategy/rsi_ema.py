from typing import Optional
import logging
import pandas as pd
from core.types import Signal, Side, Reason, StrategyParams, Position
from strategy.regimes import MarketRegime

logger = logging.getLogger(__name__)


class ReversionStrategies:
    """Regime-aware 20 EMA pullback and post-consolidation breakout signals."""

    @staticmethod
    def check_20ema_reversion(df: pd.DataFrame, symbol: str, tolerance_atr: float = 0.15) -> Optional[dict]:
        if len(df) < 21 or not {'ema_fast', 'ema_slow', 'atr'}.issubset(df.columns):
            return None
        curr, prev = df.iloc[-2], df.iloc[-3]
        ema20, ema50, atr = curr['ema_fast'], curr['ema_slow'], curr['atr']
        tolerance = max(float(atr) * tolerance_atr, 0.0)
        bullish_touch = prev['low'] <= prev['ema_fast'] + tolerance
        bearish_touch = prev['high'] >= prev['ema_fast'] - tolerance
        if curr['close'] > ema50 and bullish_touch and curr['close'] > ema20 and curr['close'] > curr['open']:
            return {"side": Side.BUY, "reason": "20 EMA Reversion (BULL)"}
        if curr['close'] < ema50 and bearish_touch and curr['close'] < ema20 and curr['close'] < curr['open']:
            return {"side": Side.SELL, "reason": "20 EMA Reversion (BEAR)"}
        return None

    @staticmethod
    def check_breakout_consolidation(
        df: pd.DataFrame, symbol: str, lookback: int = 20, consolidation_bars: int = 5,
        max_range_atr: float = 2.0, volume_multiplier: float = 1.2,
    ) -> Optional[dict]:
        required = {'high', 'low', 'close', 'atr'}
        if len(df) < lookback + consolidation_bars + 2 or not required.issubset(df.columns):
            return None
        closed = df.iloc[:-1]
        breakout = closed.iloc[-1]
        base = closed.iloc[-(consolidation_bars + 1):-1]
        prior = closed.iloc[-(lookback + consolidation_bars + 1):-(consolidation_bars + 1)]
        if prior.empty or base.empty:
            return None
        range_width = base['high'].max() - base['low'].min()
        atr = max(float(breakout['atr']), 1e-12)
        compressed = range_width <= atr * max_range_atr
        prior_high, prior_low = prior['high'].max(), prior['low'].min()
        volume_ok = True
        if 'volume' in df.columns:
            volume_ok = float(breakout['volume']) >= float(base['volume'].mean()) * volume_multiplier
        if compressed and volume_ok and breakout['close'] > prior_high:
            return {"side": Side.BUY, "reason": "Breakout Consolidation (BULL)"}
        if compressed and volume_ok and breakout['close'] < prior_low:
            return {"side": Side.SELL, "reason": "Breakout Consolidation (BEAR)"}
        return None


class RsiEmaStrategy:
    def __init__(self, tb_config: Optional[dict] = None):
        self._tb_labeler = None
        self.ema_reversion_config = (tb_config or {}).get('ema_reversion', {})
        self.breakout_config = (tb_config or {}).get('breakout_consolidation', {})
        if tb_config and tb_config.get('use_for_signals', False):
            try:
                from ml.triple_barrier import TripleBarrierLabeler, BarrierConfig
                self._tb_labeler = TripleBarrierLabeler(BarrierConfig(
                    upper_multiplier=tb_config.get('upper_multiplier', 2.0),
                    lower_multiplier=tb_config.get('lower_multiplier', 1.0),
                    max_holding_hours=tb_config.get('max_holding_hours', 48)))
            except Exception as e:
                logger.warning(f"[Strategy] TB init failed, using standard: {e}")

    MIN_SL_MULT = 1.5
    MIN_TP_MULT = 3.0

    def check_signal(self, df: pd.DataFrame, regime: MarketRegime, params: StrategyParams,
                     current_position=None, symbol: str = "BTC/USDT", allow_short: bool = True) -> Optional[Signal]:
        if df.empty or len(df) < 6:
            return None
        curr, prev = df.iloc[-2], df.iloc[-3]
        rsi, ema_fast, ema_slow, close, atr = curr['rsi'], curr['ema_fast'], curr['ema_slow'], curr['close'], curr['atr']
        atr_pct = (atr / close) * 100
        has_position = isinstance(current_position, Position) or current_position is True
        current_side = current_position.side if isinstance(current_position, Position) else (Side.BUY if current_position is True else None)

        if not has_position:
            ema_cfg = self.ema_reversion_config
            if ema_cfg.get('enabled', True):
                reversion = ReversionStrategies.check_20ema_reversion(df, symbol, ema_cfg.get('touch_tolerance_atr', 0.15))
                if reversion and (reversion['side'] == Side.BUY or allow_short):
                    return self._build_signal(symbol, reversion['side'], reversion['reason'], close, atr, params)
            breakout_cfg = self.breakout_config
            if breakout_cfg.get('enabled', True):
                breakout = ReversionStrategies.check_breakout_consolidation(
                    df, symbol, breakout_cfg.get('lookback', 20), breakout_cfg.get('consolidation_bars', 5),
                    breakout_cfg.get('max_range_atr', 2.0), breakout_cfg.get('volume_multiplier', 1.2))
                if breakout and (breakout['side'] == Side.BUY or allow_short):
                    return self._build_signal(symbol, breakout['side'], breakout['reason'], close, atr, params)
            if regime == MarketRegime.RANGING:
                return None
            last3 = df.iloc[-5:-2]
            bullish_confirmed = sum(last3['close'] > last3['open']) >= 2
            bearish_confirmed = sum(last3['close'] < last3['open']) >= 2
            trend_ok = ema_fast > ema_slow and rsi < params.rsi_entry and atr_pct < 5.0
            if trend_ok and bullish_confirmed:
                return self._build_signal(symbol, Side.BUY, Reason.SIGNAL_ENTRY, close, atr, params)
            short_ok = ema_fast < ema_slow and rsi > params.rsi_exit and atr_pct < 5.0
            if allow_short and short_ok and bearish_confirmed:
                return self._build_signal(symbol, Side.SELL, Reason.SIGNAL_ENTRY, close, atr, params)
        elif current_side == Side.BUY:
            if rsi > params.rsi_exit or ema_fast < ema_slow:
                reason = Reason.RSI_EXIT if rsi > params.rsi_exit else Reason.TREND_FLIP
                return Signal(symbol=symbol, side=Side.SELL, reason=reason, price=close, stop_loss=0, take_profit=0)
        elif current_side == Side.SELL:
            if rsi < params.rsi_entry or ema_fast > ema_slow:
                reason = Reason.RSI_EXIT if rsi < params.rsi_entry else Reason.TREND_FLIP
                return Signal(symbol=symbol, side=Side.BUY, reason=reason, price=close, stop_loss=0, take_profit=0)
        return None

    def _build_signal(self, symbol, side, reason, price, atr, params):
        sl_mult, tp_mult = max(params.sl_mult, self.MIN_SL_MULT), max(params.tp_mult, self.MIN_TP_MULT)
        if self._tb_labeler:
            barriers = self._tb_labeler.get_dynamic_barriers(entry_price=price, atr=atr, side=side.value)
            sl, tp = barriers['stop_loss'], barriers['take_profit']
        elif side == Side.BUY:
            sl, tp = price - atr * sl_mult, price + atr * tp_mult
        else:
            sl, tp = price + atr * sl_mult, price - atr * tp_mult
        return Signal(symbol=symbol, side=side, reason=reason if isinstance(reason, Reason) else Reason.SIGNAL_ENTRY,
                      price=price, stop_loss=sl, take_profit=tp, params=params)
