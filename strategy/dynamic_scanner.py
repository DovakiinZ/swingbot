"""
Dynamic Symbol Scanner — fetches all USDT pairs from MEXC, ranks by 24h volume.

Replaces static symbol list with live data from MEXC exchangeInfo + ticker endpoints.
Refreshes every 4 hours automatically. Falls back to Bybit if MEXC fails.
"""
import logging
import time
import threading
from typing import List, Optional

import ccxt

logger = logging.getLogger(__name__)

# Symbols to always exclude (stablecoins, leverage tokens)
BANNED_BASES = {'USDC', 'BUSD', 'DAI', 'TUSD', 'UST', 'FDUSD', 'USDP', 'USDD'}
BANNED_KEYWORDS = ('UP', 'DOWN', 'BEAR', 'BULL', '3L', '3S', '2L', '2S')

REFRESH_INTERVAL = 4 * 3600  # 4 hours in seconds


class DynamicScanner:
    """
    Fetches and caches the top USDT trading pairs by 24h volume.
    Thread-safe. Auto-refreshes every 4 hours.
    """

    def __init__(self, config: dict, fallback_exchange: Optional[ccxt.Exchange] = None):
        """
        Args:
            config: Full config dict with dynamic_symbols, max_symbols, min_24h_volume_usdt
            fallback_exchange: CCXT exchange to use if MEXC fetch fails (e.g. Bybit)
        """
        self.config = config
        self.fallback_exchange = fallback_exchange
        self._symbols: List[str] = []
        self._last_refresh: float = 0
        self._lock = threading.Lock()

        # MEXC exchange instance (public API, no keys needed)
        try:
            self._mexc = ccxt.mexc({'enableRateLimit': True})
            self._mexc.load_markets()
        except Exception as e:
            logger.error(f"[SCANNER] MEXC init failed: {e}")
            self._mexc = None

    @property
    def symbols(self) -> List[str]:
        """Get cached symbol list, refreshing if stale."""
        now = time.time()
        if now - self._last_refresh > REFRESH_INTERVAL or not self._symbols:
            self.refresh()
        with self._lock:
            return list(self._symbols)

    def refresh(self) -> List[str]:
        """Fetch fresh symbol list from MEXC, sorted by 24h volume."""
        max_symbols = self.config.get('max_symbols', 50)
        min_volume = self.config.get('min_24h_volume_usdt', 1_000_000)

        symbols = self._fetch_from_mexc(max_symbols, min_volume)
        if not symbols and self.fallback_exchange:
            logger.warning("[SCANNER] MEXC failed, falling back to Bybit")
            symbols = self._fetch_from_fallback(max_symbols, min_volume)

        if symbols:
            with self._lock:
                self._symbols = symbols
                self._last_refresh = time.time()

            examples = ', '.join(s.replace('/USDT', '').replace(':USDT', '') for s in symbols[:5])
            logger.warning(
                f"[SCANNER] Loaded {len(symbols)} symbols from exchange "
                f"(top {max_symbols} by volume)"
            )
            logger.warning(f"[SCANNER] Example: {examples}...")
        else:
            logger.error("[SCANNER] Could not load any symbols — using defaults")
            with self._lock:
                self._symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT']
                self._last_refresh = time.time()

        return self._symbols

    def _fetch_from_mexc(self, max_symbols: int, min_volume: float) -> List[str]:
        """Fetch from MEXC via ccxt (uses ticker endpoint for volumes)."""
        if not self._mexc:
            return []
        try:
            tickers = self._mexc.fetch_tickers()
            candidates = []

            for symbol, data in tickers.items():
                if '/USDT' not in symbol:
                    continue
                # Skip non-spot symbols
                if ':' in symbol:
                    continue

                base = symbol.split('/')[0]
                if base in BANNED_BASES:
                    continue
                if any(kw in base for kw in BANNED_KEYWORDS):
                    continue

                # Check market status
                market_info = self._mexc.markets.get(symbol, {})
                if not market_info.get('active', True):
                    continue

                vol_24h = float(data.get('quoteVolume', 0) or 0)
                if vol_24h < min_volume:
                    continue

                candidates.append({
                    'symbol': symbol,
                    'volume': vol_24h,
                })

            candidates.sort(key=lambda x: x['volume'], reverse=True)
            return [c['symbol'] for c in candidates[:max_symbols]]

        except Exception as e:
            logger.error(f"[SCANNER] MEXC fetch failed: {e}")
            return []

    def _fetch_from_fallback(self, max_symbols: int, min_volume: float) -> List[str]:
        """Fetch from fallback exchange (Bybit)."""
        try:
            tickers = self.fallback_exchange.fetch_tickers()
            candidates = []

            for symbol, data in tickers.items():
                if '/USDT' not in symbol:
                    continue

                base = symbol.split('/')[0]
                if base in BANNED_BASES:
                    continue
                if any(kw in base for kw in BANNED_KEYWORDS):
                    continue

                vol_24h = float(data.get('quoteVolume', 0) or 0)
                if vol_24h < min_volume:
                    continue

                candidates.append({
                    'symbol': symbol,
                    'volume': vol_24h,
                })

            candidates.sort(key=lambda x: x['volume'], reverse=True)
            return [c['symbol'] for c in candidates[:max_symbols]]

        except Exception as e:
            logger.error(f"[SCANNER] Fallback fetch failed: {e}")
            return []

    def get_top_pairs(self, limit: int = 20) -> List[str]:
        """Get top N symbols from cached list. Compatible with SymbolSelector interface."""
        syms = self.symbols
        return syms[:limit]
