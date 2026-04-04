"""
Multi-Exchange Market Data — fetches OHLCV, tickers, and funding rates
from Bybit, Binance, and MEXC with automatic fallback.

All public APIs — no API keys needed for market data.

Priority order for OHLCV: Bybit → Binance → MEXC
If a symbol doesn't exist on one exchange, tries the next automatically.
"""
import ccxt
import time
import logging
from typing import List, Optional, Dict
from core.types import Candle
from core.utils import safe_float

logger = logging.getLogger(__name__)

# Default exchange priority for data fetching
DEFAULT_EXCHANGES = ['bybit', 'binance', 'mexc']


class MarketData:
    """
    Multi-exchange market data source. Connects to Bybit, Binance, and MEXC
    simultaneously. Tries each exchange in priority order for OHLCV data.
    All public endpoints — no API keys required.
    """

    def __init__(self, exchange_id: str = 'bybit', sandbox: bool = False):
        self.exchange_id = exchange_id
        self.exchange = getattr(ccxt, exchange_id)({
            'enableRateLimit': True,
        })
        if sandbox:
            self.exchange.set_sandbox_mode(True)
        self.last_fetch_ts = 0

        # Initialize all 3 exchanges for fallback
        self._exchanges: Dict[str, ccxt.Exchange] = {}
        self._exchange_symbols: Dict[str, set] = {}  # exchange → set of symbols

        for eid in DEFAULT_EXCHANGES:
            try:
                ex = getattr(ccxt, eid)({'enableRateLimit': True})
                self._exchanges[eid] = ex
            except Exception as e:
                logger.warning(f"[Market] {eid} init failed: {e}")

        # Load markets for all exchanges (know which symbols exist where)
        self._load_all_markets()

    def _load_all_markets(self) -> None:
        """Load available symbols from all exchanges."""
        for eid, ex in self._exchanges.items():
            try:
                markets = ex.load_markets()
                symbols = {s for s in markets.keys() if '/USDT' in s}
                self._exchange_symbols[eid] = symbols
                logger.info(f"[Market] {eid}: {len(symbols)} USDT symbols loaded")
            except Exception as e:
                logger.warning(f"[Market] {eid} market load failed: {e}")
                self._exchange_symbols[eid] = set()

    def _get_exchange_for_symbol(self, symbol: str) -> Optional[ccxt.Exchange]:
        """Find the best exchange that has this symbol, in priority order."""
        # Try primary first
        if symbol in self._exchange_symbols.get(self.exchange_id, set()):
            return self._exchanges.get(self.exchange_id, self.exchange)

        # Try others in priority order
        for eid in DEFAULT_EXCHANGES:
            if eid == self.exchange_id:
                continue
            if symbol in self._exchange_symbols.get(eid, set()):
                return self._exchanges.get(eid)

        # Last resort: return primary and let it fail naturally
        return self.exchange

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> List[Candle]:
        """
        Fetch OHLCV candles. Tries all exchanges in priority order.
        Returns candles from the first exchange that has the symbol.
        """
        now = time.time()
        if now - self.last_fetch_ts < 0.3:
            time.sleep(0.3)

        # Build list of exchanges to try
        exchanges_to_try = []
        for eid in DEFAULT_EXCHANGES:
            ex = self._exchanges.get(eid)
            if ex and symbol in self._exchange_symbols.get(eid, set()):
                exchanges_to_try.append((eid, ex))

        # If no exchange claims to have it, try primary anyway
        if not exchanges_to_try:
            exchanges_to_try = [(self.exchange_id, self.exchange)]

        last_error = None
        for eid, ex in exchanges_to_try:
            try:
                ohlcv = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
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
                last_error = e
                logger.debug(f"[{eid}] OHLCV failed for {symbol}: {e}")
                continue

        # All exchanges failed
        logger.error(f"[Market] OHLCV failed on all exchanges for {symbol}: {last_error}")
        raise last_error or Exception(f"No exchange has {symbol}")

    def fetch_htf_trend(self, symbol: str, timeframe: str = '4h',
                        ema_period: int = 200) -> Dict:
        """
        Fetch higher-timeframe trend direction for MTF confluence.
        Returns dict with: trend ('up'|'down'|'flat'), ema_value, close, above_ema.
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

            if close > ema * 1.002:
                trend = 'up'
            elif close < ema * 0.998:
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
            logger.warning(f"[Market] HTF trend fetch failed for {symbol}: {e}")
            return {'trend': 'flat', 'ema_value': 0, 'close': 0, 'above_ema': None}

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        """
        Fetch the current perpetual funding rate for a symbol.
        Only works on perpetual futures symbols (e.g. BTC/USDT:USDT).
        """
        try:
            if ':' not in symbol:
                return None

            # Try primary exchange first, then others
            for eid in DEFAULT_EXCHANGES:
                ex = self._exchanges.get(eid)
                if not ex:
                    continue
                if symbol not in self._exchange_symbols.get(eid, set()):
                    continue
                try:
                    data = ex.fetch_funding_rate(symbol)
                    rate = data.get('fundingRate') or data.get('funding_rate')
                    return float(rate) if rate is not None else None
                except Exception:
                    continue
            return None
        except Exception as e:
            logger.debug(f"[Market] Funding rate unavailable for {symbol}: {e}")
            return None

    def fetch_tickers_for_universe(self, min_volume_usdt: float = 10_000_000,
                                   banned: Optional[List[str]] = None) -> List[str]:
        """Fetch top USDT pairs by volume from primary exchange."""
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
            logger.error(f"[Market] Ticker fetch failed: {e}")
            return []

    def get_market_structure(self, symbol: str) -> dict:
        """Get market structure (min qty, tick size, etc) for a symbol."""
        ex = self._get_exchange_for_symbol(symbol)
        try:
            markets = ex.load_markets()
            return markets.get(symbol, {})
        except Exception as e:
            logger.error(f"[Market] Market structure fetch failed for {symbol}: {e}")
            return {}

    def get_available_exchanges(self, symbol: str) -> List[str]:
        """Return which exchanges have this symbol."""
        return [eid for eid, syms in self._exchange_symbols.items() if symbol in syms]
