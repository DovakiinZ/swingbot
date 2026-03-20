"""
ml/backtester.py -- Backtest strategy using Triple-Barrier labeling.

Runs the full strategy on historical data and generates
Triple-Barrier labeled results for model training.

Usage:
    python -m ml.backtester --symbol BTC/USDT --days 90
    python -m ml.backtester --all-symbols --days 30
"""
import argparse
import logging
import yaml
import pandas as pd
from data.market import MarketData
from data.features import FeatureEngine
from ml.triple_barrier import TripleBarrierLabeler, BarrierConfig
from strategy.scanner import MarketScanner
from strategy.regimes import RegimeDetector

logger = logging.getLogger(__name__)


def backtest_symbol(
    symbol: str,
    timeframe: str,
    lookback_days: int,
    tb_config: BarrierConfig
) -> pd.DataFrame:
    """
    Fetch historical data for symbol and apply:
    1. Feature engineering
    2. Scanner scoring
    3. Triple-Barrier labeling

    Returns DataFrame ready for model training.
    """
    market = MarketData()
    scanner = MarketScanner()
    labeler = TripleBarrierLabeler(tb_config)

    # Fetch enough candles
    limit = lookback_days * 24   # 1h candles
    candles = market.fetch_ohlcv(symbol, timeframe, limit=limit)
    if not candles:
        print(f"{symbol}: No candle data available")
        return pd.DataFrame()

    df = FeatureEngine.compute_indicators(candles)
    if df.empty:
        print(f"{symbol}: Feature computation returned empty")
        return pd.DataFrame()

    # Apply triple-barrier labels to entire dataset
    df['tb_label'] = labeler.label_dataset(df)

    # Add scanner scores
    scores = []
    for i in range(len(df)):
        if i < 50:
            scores.append(0)
            continue
        slice_df = df.iloc[max(0, i - 100):i + 1]
        try:
            regime = RegimeDetector.detect(slice_df.iloc[-1])
            score, _ = scanner.score_symbol(slice_df, regime)
            scores.append(score)
        except Exception:
            scores.append(0)
    df['scanner_score'] = scores

    # Filter: only label rows where score >= 65 (actual trade candidates)
    df_filtered = df[df['scanner_score'] >= 65].copy()
    df_filtered = df_filtered.dropna(subset=['tb_label'])

    print(f"{symbol}: {len(df_filtered)} labeled samples")
    if len(df_filtered) > 0:
        print(f"  +1 (win):  {(df_filtered['tb_label'] == 1).sum()}")
        print(f"   0 (time): {(df_filtered['tb_label'] == 0).sum()}")
        print(f"  -1 (loss): {(df_filtered['tb_label'] == -1).sum()}")

    return df_filtered


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Backtest with Triple-Barrier labeling")
    parser.add_argument('--symbol', default='BTC/USDT', help='Symbol to backtest')
    parser.add_argument('--days', type=int, default=90, help='Days of history')
    parser.add_argument('--all-symbols', action='store_true',
                        help='Backtest BTC, ETH, SOL')
    args = parser.parse_args()

    with open('config.yaml') as f:
        config = yaml.safe_load(f)

    tb_conf = config.get('triple_barrier', {})
    tb_config = BarrierConfig(
        upper_multiplier=tb_conf.get('upper_multiplier', 2.0),
        lower_multiplier=tb_conf.get('lower_multiplier', 1.0),
        max_holding_hours=tb_conf.get('max_holding_hours', 48)
    )

    symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'] if args.all_symbols else [args.symbol]
    total_samples = 0

    for sym in symbols:
        print(f"\n--- Backtesting {sym} ({args.days} days) ---")
        df = backtest_symbol(sym, config['timeframe'], args.days, tb_config)
        total_samples += len(df)

    print(f"\nTotal training samples: {total_samples}")
    print("Run 'python -m ml.trainer' to train the model on this data.")
