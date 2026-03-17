from core.types import StrategyParams
import copy

# Base defaults
DEFAULT_PARAMS = StrategyParams(
    rsi_period=14,
    rsi_entry=30,
    rsi_exit=70,
    ema_fast=20,
    ema_slow=50,
    atr_period=14,
    sl_mult=2.0,
    tp_mult=3.0
)

# Define 8-20 arms
# Varying RSI thresholds, periods, and SL/TP multipliers
ARMS = []

# Arm 1: Default
ARMS.append(copy.deepcopy(DEFAULT_PARAMS))

# Arm 2: Sensitive RSI
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_period = 10
params.rsi_entry = 35
ARMS.append(params)

# Arm 3: Conservative RSI
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_period = 21
params.rsi_entry = 25
ARMS.append(params)

# Arm 4: Wider Stops (Trend Following)
params = copy.deepcopy(DEFAULT_PARAMS)
params.sl_mult = 3.0
params.tp_mult = 5.0
ARMS.append(params)

# Arm 5: Tight Scalp
params = copy.deepcopy(DEFAULT_PARAMS)
params.sl_mult = 1.5
params.tp_mult = 2.0
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

# Arm 8: High Volatility Setup
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_entry = 20 # Deep oversold
params.sl_mult = 4.0  # Wide stop
ARMS.append(params)

def get_arm(index: int) -> StrategyParams:
    if 0 <= index < len(ARMS):
        return ARMS[index]
    return DEFAULT_PARAMS
