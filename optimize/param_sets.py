from core.types import StrategyParams
import copy

# Base defaults – conservative RSI for choppy markets
DEFAULT_PARAMS = StrategyParams(
    rsi_period=14,
    rsi_entry=35,    # Conservative entry RSI – 35% below 50 (oversold)
    rsi_exit=65,     # Conservative exit RSI – 65% above 50 (overbought)
    ema_fast=20,
    ema_slow=50,
    atr_period=14,
    sl_mult=2.0,
    tp_mult=5.0      # R:R ~2.5:1 baseline
)

# Define arms – retain previous arms and add choppy‑market focused arms
ARMS = []

# Arm 1: Default (conservative RSI)
ARMS.append(copy.deepcopy(DEFAULT_PARAMS))

# Arm 2: Sensitive RSI
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_period = 10
params.rsi_entry = 32
params.rsi_exit = 68
ARMS.append(params)

# Arm 3: Conservative RSI (longer period)
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_period = 21
params.rsi_entry = 30
params.rsi_exit = 70
ARMS.append(params)

# Arm 4: Wide stops, big targets – trend‑following mode
params = copy.deepcopy(DEFAULT_PARAMS)
params.sl_mult = 3.0
params.tp_mult = 8.0
ARMS.append(params)

# Arm 5: Tight scalp – fast entries
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

# Arm 8: High Volatility – deeper oversold
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_entry = 28   
params.sl_mult   = 4.0  
params.tp_mult   = 10.0 
ARMS.append(params)

# --- CHOPPY MARKET OPTIMIZATIONS ---

# Arm 9: Mean Reversion Choppy (tight RSI, tight SL)
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_entry = 38
params.rsi_exit = 62
params.sl_mult = 1.2
params.tp_mult = 3.0
ARMS.append(params)

# Arm 10: High Frequency Choppy (fast RSI, tight EMA)
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_period = 7
params.rsi_entry = 34
params.rsi_exit = 66
params.ema_fast = 5
params.ema_slow = 13
params.sl_mult = 1.0
params.tp_mult = 2.5
ARMS.append(params)

# Arm 11: Range‑Bound Volatility (mid RSI, ATR focused SL)
params = copy.deepcopy(DEFAULT_PARAMS)
params.rsi_period = 14
params.rsi_entry = 35
params.rsi_exit = 65
params.atr_period = 10
params.sl_mult = 1.8
params.tp_mult = 4.5
ARMS.append(params)

def get_arm(index: int) -> StrategyParams:
    if 0 <= index < len(ARMS):
        return ARMS[index]
    return DEFAULT_PARAMS
