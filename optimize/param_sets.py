from core.types import StrategyParams
import copy

# Base defaults
DEFAULT_PARAMS = StrategyParams(
    rsi_period=14,
    rsi_entry=45,    # Widened for paper mode — RSI < 45 triggers long (was 35, too strict)
    rsi_exit=55,     # Widened for paper mode — RSI > 55 triggers short (was 70, too strict)
    ema_fast=20,
    ema_slow=50,
    atr_period=14,
    sl_mult=2.0,
    tp_mult=5.0      # Was 3.0 (R:R=1.5). Now 2.5:1 — research minimum for breakout profitability
)

# Define 8-20 arms
# Varying RSI thresholds, periods, and SL/TP multipliers
ARMS = []

# Arm 1: Default
ARMS.append(copy.deepcopy(DEFAULT_PARAMS))

# Arm 2: Sensitive RSI
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_period = 10
params.rsi_entry = 48
ARMS.append(params)

# Arm 3: Conservative RSI
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_period = 21
params.rsi_entry = 40
ARMS.append(params)

# Arm 4: Wide stops, big targets — trend-following mode, sl=3.0/tp=8.0 → R:R=2.67
params = copy.deepcopy(DEFAULT_PARAMS)
params.sl_mult = 3.0
params.tp_mult = 8.0
ARMS.append(params)

# Arm 5: Tight scalp — fast entries, sl=1.5/tp=4.0 → R:R=2.67
params = copy.deepcopy(DEFAULT_PARAMS)
params.sl_mult = 1.5
params.tp_mult = 4.0
ARMS.append(params)

# Arm 6: Golden Cross focus (Slow Trend)
params = copy.deepcopy(DEFAULT_PARAMS)
params.ema_fast = 50
params.ema_slow = 200
ARMS.append(params)

# Arm 7: Quick EMA
params = copy.deepcopy(DEFAULT_PARAMS)
params.ema_fast = 9
params.ema_slow = 21
ARMS.append(params)

# Arm 8: High Volatility — deeper oversold, sl=4.0/tp=10.0 → R:R=2.5
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_entry = 35   # More selective than default but still reachable
params.sl_mult   = 4.0  # Wide stop for volatile assets
params.tp_mult   = 10.0 # Big target to compensate wide stop
ARMS.append(params)

def get_arm(index: int) -> StrategyParams:
    if 0 <= index < len(ARMS):
        return ARMS[index]
    return DEFAULT_PARAMS
