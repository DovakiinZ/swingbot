import ccxt
import time
import logging
from typing import List, Optional
from core.types import ScanResult
from data.features import FeatureEngine
from data.market import MarketData
from strategy.regimes import RegimeDetector, MarketRegime

logger = logging.getLogger(__name__)

class SymbolSelector:
    """
    Scans the market to find high-quality trading pairs.
    Filters:
    - USDT pairs only
    - Top N by Volume (Liquidity)
    - Exclude Stablecoins
    """
    def __init__(self, exchange: ccxt.Exchange, market: Optional[MarketData] = None):
        self.exchange = exchange
        self.market = market
        self.banned_coins = ['USDC', 'BUSD', 'DAI', 'TUSD', 'UST', 'FDUSD']

    def get_top_pairs(self, limit: int = 5, min_volume_usdt: float = 10_000_000) -> List[str]:
        try:
            tickers = self.exchange.fetch_tickers()

            candidates = []

            for symbol, data in tickers.items():
                if not symbol.endswith('/USDT'):
                    continue

                base = symbol.split('/')[0]
                if any(x in base for x in ['UP', 'DOWN', 'BEAR', 'BULL']) or base in self.banned_coins:
                    continue

                quote_vol = data.get('quoteVolume', 0)
                if quote_vol < min_volume_usdt:
                    continue

                candidates.append({
                    'symbol': symbol,
                    'volume': quote_vol,
                    'change': data.get('percentage', 0)
                })

            candidates.sort(key=lambda x: x['volume'], reverse=True)

            top_pairs = [c['symbol'] for c in candidates[:limit]]
            logger.info(f"Top {limit} Pairs by Volume: {top_pairs}")
            return top_pairs

        except Exception as e:
            logger.error(f"Error selecting pairs: {e}")
            return ['BTC/USDT']

    def scan_and_rank(self,
                      scan_pairs_count: int = 30,
                      min_volume_usdt: float = 10_000_000,
                      rank_top_n: int = 10,
                      timeframe: str = "1h",
                      lookback: int = 100) -> List[ScanResult]:
        """
        Scan market for top candidates, compute indicators, score and rank them.

        Scoring weights:
        - RSI depth (distance below 50): 30%
        - Trend alignment (EMA fast > slow): 25%
        - Volume rank (normalized): 20%
        - Volatility (moderate ATR): 15%
        - Regime (trending preferred): 10%
        """
        if not self.market:
            logger.warning("scan_and_rank requires MarketData instance")
            return []

        try:
            # Step 1: Fetch tickers (1 API call), filter USDT spot pairs by volume
            tickers = self.exchange.fetch_tickers()

            candidates = []
            for symbol, data in tickers.items():
                if not symbol.endswith('/USDT'):
                    continue

                base = symbol.split('/')[0]
                if any(x in base for x in ['UP', 'DOWN', 'BEAR', 'BULL']) or base in self.banned_coins:
                    continue

                quote_vol = data.get('quoteVolume', 0)
                if quote_vol < min_volume_usdt:
                    continue

                candidates.append({
                    'symbol': symbol,
                    'volume': quote_vol,
                    'last': data.get('last', 0),
                })

            candidates.sort(key=lambda x: x['volume'], reverse=True)

            # Cap at 2x limit to avoid too many API calls
            fetch_limit = min(len(candidates), scan_pairs_count * 2)
            candidates = candidates[:fetch_limit]

            if not candidates:
                logger.warning("No candidates found in scan")
                return []

            # Step 2: Fetch OHLCV and compute indicators for each candidate
            scanned_at = int(time.time() * 1000)
            results: List[ScanResult] = []

            for rank, cand in enumerate(candidates):
                symbol = cand['symbol']
                try:
                    candles = self.market.fetch_ohlcv(symbol, timeframe, limit=lookback)
                    if not candles or len(candles) < 50:
                        continue

                    df = FeatureEngine.compute_indicators(candles)
                    if df.empty:
                        continue

                    curr = df.iloc[-1]
                    rsi = curr.get('rsi', 50)
                    ema_fast = curr.get('ema_fast', 0)
                    ema_slow = curr.get('ema_slow', 0)
                    atr = curr.get('atr', 0)
                    close = curr.get('close', 1)
                    atr_pct = (atr / close) * 100 if close > 0 else 0

                    regime = RegimeDetector.detect(curr)

                    # Skip high volatility
                    if regime == MarketRegime.HIGH_VOLATILITY:
                        continue

                    # Trend direction
                    if ema_fast > ema_slow:
                        trend = "UP"
                    elif ema_fast < ema_slow:
                        trend = "DOWN"
                    else:
                        trend = "FLAT"

                    # --- Scoring ---
                    # RSI depth (30%): lower RSI = higher score, max at RSI=20
                    rsi_score = max(0, min(1.0, (50 - rsi) / 30)) if rsi < 50 else 0.0

                    # Trend alignment (25%): UP = 1.0, FLAT = 0.3, DOWN = 0.0
                    trend_score = 1.0 if trend == "UP" else (0.3 if trend == "FLAT" else 0.0)

                    # Volume rank (20%): normalized position in sorted list
                    volume_score = max(0, 1.0 - (rank / max(len(candidates), 1)))

                    # Volatility (15%): moderate ATR (1-3%) is ideal
                    if 1.0 <= atr_pct <= 3.0:
                        vol_score = 1.0
                    elif atr_pct < 1.0:
                        vol_score = atr_pct  # low vol = lower score
                    else:
                        vol_score = max(0, 1.0 - (atr_pct - 3.0) / 2.0)

                    # Regime (10%): TRENDING = 1.0, RANGING = 0.5, UNCERTAIN = 0.3
                    regime_scores = {
                        MarketRegime.TRENDING: 1.0,
                        MarketRegime.RANGING: 0.5,
                        MarketRegime.UNCERTAIN: 0.3,
                        MarketRegime.HIGH_VOLATILITY: 0.0,
                    }
                    regime_score = regime_scores.get(regime, 0.3)

                    # Weighted total
                    total_score = (
                        rsi_score * 0.30 +
                        trend_score * 0.25 +
                        volume_score * 0.20 +
                        vol_score * 0.15 +
                        regime_score * 0.10
                    )

                    results.append(ScanResult(
                        symbol=symbol,
                        score=round(total_score, 4),
                        rsi=round(rsi, 2),
                        atr_pct=round(atr_pct, 2),
                        volume_rank=rank + 1,
                        trend=trend,
                        regime=regime.value,
                        scanned_at=scanned_at,
                    ))

                except Exception as e:
                    logger.debug(f"Skipping {symbol} in scan: {e}")
                    continue

            # Sort by score descending, return top N
            results.sort(key=lambda x: x.score, reverse=True)
            results = results[:rank_top_n]

            logger.info(f"Scan complete: {len(results)} candidates ranked")
            for r in results[:5]:
                logger.info(f"  {r.symbol}: score={r.score} rsi={r.rsi} trend={r.trend} regime={r.regime}")

            return results

        except Exception as e:
            logger.error(f"Error in scan_and_rank: {e}")
            return []
