"""
Multi-Exchange Dynamic Symbol Scanner.

Aggregates USDT trading pairs from Binance, Bybit, and MEXC simultaneously.
Merges 24h volumes across all exchanges for accurate ranking.
Refreshes every 4 hours. All public APIs — no keys needed.

Symbol universe flow:
  1. Fetch tickers from Binance, Bybit, MEXC in parallel (all public)
  2. Merge by base currency, sum volumes across exchanges
  3. Filter: min volume, active status, no stablecoins/leverage tokens
  4. Sort by combined 24h volume descending
  5. Return top N (default 50)
"""
import logging
import time
import threading
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import ccxt

logger = logging.getLogger(__name__)

BANNED_BASES = {'USDC', 'BUSD', 'DAI', 'TUSD', 'UST', 'FDUSD', 'USDP', 'USDD', 'PYUSD'}
BANNED_KEYWORDS = ('UP', 'DOWN', 'BEAR', 'BULL', '3L', '3S', '2L', '2S', '5L', '5S')

REFRESH_INTERVAL = 4 * 3600  # 4 hours

# Exchanges to aggregate (all public, no keys)
EXCHANGE_IDS = ['binance', 'bybit', 'mexc']


class DynamicScanner:
    """
    Fetches and caches the top USDT trading pairs by combined 24h volume
    across Binance, Bybit, and MEXC. Thread-safe. Auto-refreshes every 4h.
    """

    def __init__(self, config: dict, fallback_exchange: Optional[ccxt.Exchange] = None,
                 data_exchange: Optional[ccxt.Exchange] = None):
        self.config = config
        self.fallback_exchange = fallback_exchange
        self.data_exchange = data_exchange  # Exchange used for OHLCV (must have the symbol)
        self._symbols: List[str] = []
        self._exchange_map: Dict[str, List[str]] = {}  # symbol → list of exchanges that have it
        self._data_exchange_symbols: set = set()  # symbols available on the data exchange
        self._last_refresh: float = 0
        self._lock = threading.Lock()

        # Initialize exchange connections (public only)
        self._exchanges: Dict[str, ccxt.Exchange] = {}
        for eid in EXCHANGE_IDS:
            try:
                ex = getattr(ccxt, eid)({'enableRateLimit': True})
                self._exchanges[eid] = ex
                logger.info(f"[SCANNER] {eid} connected (public API)")
            except Exception as e:
                logger.warning(f"[SCANNER] {eid} init failed: {e}")

        # Load available symbols from the data exchange (for filtering)
        self._load_data_exchange_symbols()

    def _load_data_exchange_symbols(self) -> None:
        """Load the list of symbols available on the data exchange (Bybit)."""
        ex = self.data_exchange
        if not ex:
            # Fall back to the bybit instance we created
            ex = self._exchanges.get('bybit')
        if not ex:
            return
        try:
            markets = ex.load_markets()
            self._data_exchange_symbols = {
                sym for sym in markets.keys() if '/USDT' in sym
            }
            logger.info(f"[SCANNER] Data exchange has {len(self._data_exchange_symbols)} USDT symbols")
        except Exception as e:
            logger.warning(f"[SCANNER] Could not load data exchange markets: {e}")

    @property
    def symbols(self) -> List[str]:
        """Get cached symbol list, refreshing if stale."""
        now = time.time()
        if now - self._last_refresh > REFRESH_INTERVAL or not self._symbols:
            self.refresh()
        with self._lock:
            return list(self._symbols)

    def refresh(self) -> List[str]:
        """Fetch from all exchanges in parallel, merge, and rank."""
        max_symbols = self.config.get('max_symbols', 50)
        min_volume = self.config.get('min_24h_volume_usdt', 1_000_000)

        # Fetch tickers from all exchanges in parallel
        all_tickers: Dict[str, Dict] = {}  # exchange_id → {symbol: ticker_data}

        def _fetch_one(eid: str):
            ex = self._exchanges.get(eid)
            if not ex:
                return eid, {}
            try:
                tickers = ex.fetch_tickers()
                return eid, tickers
            except Exception as e:
                logger.warning(f"[SCANNER] {eid} ticker fetch failed: {e}")
                return eid, {}

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_fetch_one, eid): eid for eid in self._exchanges}
            for future in as_completed(futures, timeout=30):
                try:
                    eid, tickers = future.result()
                    if tickers:
                        all_tickers[eid] = tickers
                        logger.info(f"[SCANNER] {eid}: {len(tickers)} tickers loaded")
                except Exception as e:
                    logger.warning(f"[SCANNER] Ticker future failed: {e}")

        if not all_tickers:
            logger.error("[SCANNER] All exchanges failed — using defaults")
            with self._lock:
                self._symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
                                 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'DOT/USDT', 'MATIC/USDT']
                self._last_refresh = time.time()
            return self._symbols

        # Merge: aggregate volume by base currency across all exchanges
        merged: Dict[str, Dict] = {}  # base → {total_vol, symbol, exchanges}

        for eid, tickers in all_tickers.items():
            for symbol, data in tickers.items():
                if '/USDT' not in symbol:
                    continue
                # Skip derivatives for symbol discovery
                if ':' in symbol:
                    continue

                base = symbol.split('/')[0]
                if base in BANNED_BASES:
                    continue
                if any(kw in base for kw in BANNED_KEYWORDS):
                    continue

                vol_24h = float(data.get('quoteVolume', 0) or 0)

                if base not in merged:
                    merged[base] = {
                        'symbol': f"{base}/USDT",
                        'total_volume': 0,
                        'exchanges': [],
                        'last_price': 0,
                    }
                merged[base]['total_volume'] += vol_24h
                merged[base]['exchanges'].append(eid)
                price = float(data.get('last', 0) or 0)
                if price > 0:
                    merged[base]['last_price'] = price

        # Filter: min volume + must exist on data exchange (where we fetch OHLCV)
        candidates = []
        skipped_no_data = 0
        for v in merged.values():
            if v['total_volume'] < min_volume:
                continue
            # Only include if the data exchange has this symbol
            if self._data_exchange_symbols and v['symbol'] not in self._data_exchange_symbols:
                skipped_no_data += 1
                continue
            candidates.append(v)

        if skipped_no_data:
            logger.info(f"[SCANNER] Skipped {skipped_no_data} symbols not on data exchange")
        candidates.sort(key=lambda x: x['total_volume'], reverse=True)
        candidates = candidates[:max_symbols]

        symbols = [c['symbol'] for c in candidates]
        exchange_map = {c['symbol']: c['exchanges'] for c in candidates}

        with self._lock:
            self._symbols = symbols
            self._exchange_map = exchange_map
            self._last_refresh = time.time()

        # Log summary
        sources = {eid for eid in all_tickers}
        examples = ', '.join(s.replace('/USDT', '') for s in symbols[:8])
        logger.warning(
            f"[SCANNER] Loaded {len(symbols)} symbols from {', '.join(sources)} "
            f"(top {max_symbols} by combined volume)"
        )
        logger.warning(f"[SCANNER] Example: {examples}...")

        if candidates:
            top = candidates[0]
            logger.info(
                f"[SCANNER] #1: {top['symbol']} — "
                f"${top['total_volume']:,.0f} vol across {', '.join(top['exchanges'])}"
            )

        return symbols

    def get_top_pairs(self, limit: int = 20) -> List[str]:
        """Get top N symbols from cached list. Compatible with SymbolSelector interface."""
        syms = self.symbols
        return syms[:limit]

    def get_exchanges_for_symbol(self, symbol: str) -> List[str]:
        """Return which exchanges list this symbol."""
        with self._lock:
            return self._exchange_map.get(symbol, [])
