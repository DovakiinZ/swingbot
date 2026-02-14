import ccxt
import time
import logging
from typing import List, Optional
from core.types import Candle
from core.utils import safe_float

logger = logging.getLogger(__name__)

class MarketData:
    def __init__(self, exchange_id: str = 'binance', sandbox: bool = False):
        self.exchange = getattr(ccxt, exchange_id)({
            'enableRateLimit': True,
        })
        if sandbox:
            self.exchange.set_sandbox_mode(True)
        self.last_fetch_ts = 0

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> List[Candle]:
        try:
            # Rate limit handling is built-in to ccxt if enableRateLimit=True
            # But we can add extra safety
            now = time.time()
            if now - self.last_fetch_ts < 0.5:
                time.sleep(0.5)
            
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            self.last_fetch_ts = time.time()
            
            candles = []
            for row in ohlcv:
                # row: [timestamp, open, high, low, close, volume]
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
            logger.error(f"Error fetching OHLCV for {symbol}: {e}")
            raise

    def get_market_structure(self, symbol: str) -> dict:
        try:
            markets = self.exchange.load_markets()
            return markets[symbol]
        except Exception as e:
            logger.error(f"Error fetching market structure for {symbol}: {e}")
            return {}
