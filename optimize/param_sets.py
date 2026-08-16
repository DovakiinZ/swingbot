from core.types import StrategyParams
import copy

# Conservative defaults for choppy conditions: fewer false entries and lower risk.
DEFAULT_PARAMS = StrategyParams(rsi_period=14, rsi_entry=35, rsi_exit=65, ema_fast=20, ema_slow=50, atr_period=14, sl_mult=2.0, tp_mult=5.0)

ARMS = []

def _add(**changes):
    params = copy.deepcopy(DEFAULT_PARAMS)
    for key, value in changes.items():
        setattr(params, key, value)
    ARMS.append(params)

_add()
_add(rsi_period=10, rsi_entry=32, rsi_exit=68)
_add(rsi_period=21, rsi_entry=30, rsi_exit=70)
_add(sl_mult=3.0, tp_mult=8.0)
_add(sl_mult=1.5, tp_mult=4.0)
_add(ema_fast=50, ema_slow=200)
_add(ema_fast=9, ema_slow=21)
_add(rsi_entry=28, sl_mult=4.0, tp_mult=10.0)
# Choppy-market arms: bounded RSI entries, with the 20 EMA as the mean-reversion anchor.
_add(rsi_entry=38, rsi_exit=62, sl_mult=1.5, tp_mult=3.5)
_add(rsi_period=7, rsi_entry=36, rsi_exit=64, ema_fast=20, ema_slow=50, sl_mult=1.5, tp_mult=3.0)
_add(rsi_period=14, rsi_entry=35, rsi_exit=65, atr_period=10, sl_mult=1.8, tp_mult=4.5)
# Transition arm: wider bounds but slower confirmation to avoid regime-switch overtrading.
_add(rsi_period=21, rsi_entry=34, rsi_exit=66, ema_fast=20, ema_slow=50, sl_mult=2.2, tp_mult=5.0)

def get_arm(index: int) -> StrategyParams:
    return ARMS[index] if 0 <= index < len(ARMS) else DEFAULT_PARAMS
