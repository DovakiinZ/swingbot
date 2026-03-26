import ccxt
import time
import logging
from typing import List, Optional, Dict
from core.types import Candle
from core.utils import safe_float

logger = logging.getLogger(__name__)


class MarketData:
    """
    Read-only market data source. Connects to any ccxt-supported exchange
    without API keys — public endpoints only (OHLCV, tickers, funding rates).

    Can be instantiated for multiple exchanges simultaneously:
      - One for MEXC (trading symbol universe)
      - One for Bybit (higher-quality data, funding rates, OI)
      - One for Binance (largest liquidity reference)
    """

    def __init__(self, exchange_id: str = 'bybit', sandbox: bool = False):
        self.exchange_id = exchange_id
        self.exchange = getattr(ccxt, exchange_id)({
            'enableRateLimit': True,
        })
        if sandbox:
            self.exchange.set_sandbox_mode(True)
        self.last_fetch_ts = 0

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> List[Candle]:
        try:
            now = time.time()
            if now - self.last_fetch_ts < 0.5:
                time.sleep(0.5)

            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            self.last_fetch_ts = time.time()

            candles = []
            for row in ohlcv:
                candles.append(Candle(
                    timestamp=int(row[0]),
                    open=safe_float(row[1]),
                    high=safe_float(row[2]),
                    low=safe_float(row[3]),
                    close=safe_float(row[4]),
                    volume=safe_float(row[5])
                ))
            return candles
        except Exception as e:
            logger.error(f"[{self.exchange_id}] Error fetching OHLCV for {symbol}: {e}")
            raise

    def fetch_htf_trend(self, symbol: str, timeframe: str = '4h',
                        ema_period: int = 200) -> Dict:
        """
        Fetch higher-timeframe trend direction for MTF confluence.
        Returns dict with: trend ('up'|'down'|'flat'), ema_value, close, above_ema.

        Used as a prerequisite filter: only take 1H long signals when 4H trend is up.
        Research: Adding HTF filter raises Profit Factor from ~1.4 to ~2.0+.
        """
        try:
            candles = self.fetch_ohlcv(symbol, timeframe, limit=max(ema_period + 10, 220))
            if len(candles) < ema_period:
                return {'trend': 'flat', 'ema_value': 0, 'close': 0, 'above_ema': None}

            closes = [c.close for c in candles]

            # EMA calculation
            k = 2.0 / (ema_period + 1)
            ema = closes[0]
            for price in closes[1:]:
                ema = price * k + ema * (1 - k)

            close = closes[-1]
            prev_close = closes[-2]

            if close > ema * 1.002:       # 0.2% above → uptrend
                trend = 'up'
            elif close < ema * 0.998:     # 0.2% below → downtrend
                trend = 'down'
            else:
                trend = 'flat'

            return {
                'trend': trend,
                'ema_value': round(ema, 6),
                'close': close,
                'above_ema': close > ema,
                'prev_close': prev_close,
            }
        except Exception as e:
            logger.warning(f"[{self.exchange_id}] HTF trend fetch failed for {symbol}: {e}")
            return {'trend': 'flat', 'ema_value': 0, 'close': 0, 'above_ema': None}

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        """
        Fetch the current perpetual funding rate for a symbol.
        Only works on perpetual futures symbols (e.g. BTC/USDT:USDT).
        Returns funding rate as a float (e.g. 0.0001 = 0.01% per 8h).
        Returns None if unavailable (spot symbols, exchange not supported).

        Interpretation:
          > +0.10%  → longs are dangerously crowded, expect a squeeze DOWN
          > +0.05%  → longs slightly overcrowded, avoid new longs
            ~0.01%  → neutral, no bias
          < -0.05%  → shorts are overcrowded, expect a squeeze UP
          < -0.10%  → extreme short crowding, strong long opportunity
        """
        try:
            # Only perpetual symbols have funding rates
            if ':' not in symbol:
                return None

            data = self.exchange.fetch_funding_rate(symbol)
            rate = data.get('fundingRate') or data.get('funding_rate')
            return float(rate) if rate is not None else None
        except Exception as e:
            logger.debug(f"[{self.exchange_id}] Funding rate unavailable for {symbol}: {e}")
            return None

    def fetch_tickers_for_universe(self, min_volume_usdt: float = 10_000_000,
                                   banned: Optional[List[str]] = None) -> List[str]:
        """
        Fetch top USDT pairs by volume from this exchange.
        Used by SymbolSelector when scanning for opportunities.
        """
        if banned is None:
            banned = ['USDC', 'BUSD', 'DAI', 'TUSD', 'UST', 'FDUSD']
        try:
            tickers = self.exchange.fetch_tickers()
            candidates = []
            for symbol, data in tickers.items():
                if '/USDT' not in symbol:
                    continue
                base = symbol.split('/')[0]
                if any(x in base for x in ['UP', 'DOWN', 'BEAR', 'BULL']) or base in banned:
                    continue
                vol = data.get('quoteVolume', 0) or 0
                if vol < min_volume_usdt:
                    continue
                candidates.append((symbol, vol))
            candidates.sort(key=lambda x: x[1], reverse=True)
            return [s for s, _ in candidates]
        except Exception as e:
            logger.error(f"[{self.exchange_id}] Ticker fetch failed: {e}")
            return []

    def get_market_structure(self, symbol: str) -> dict:
        try:
            markets = self.exchange.load_markets()
            return markets.get(symbol, {})
        except Exception as e:
            logger.error(f"[{self.exchange_id}] Market structure fetch failed for {symbol}: {e}")
            return {}
