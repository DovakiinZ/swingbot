import ccxt
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

class SymbolSelector:
    """
    Scans the market to find high-quality trading pairs.
    Filters:
    - USDT pairs only
    - Top N by Volume (Liquidity)
    - Exclude Stablecoins
    """
    def __init__(self, exchange: ccxt.Exchange):
        self.exchange = exchange
        self.banned_coins = ['USDC', 'BUSD', 'DAI', 'TUSD', 'UST', 'FDUSD']

    def get_top_pairs(self, limit: int = 5, min_volume_usdt: float = 10_000_000) -> List[str]:
        try:
            # 1. Fetch all tickers (lightweight generally, or load_markets then fetch specific?)
            # fetch_tickers is efficient on Binance
            tickers = self.exchange.fetch_tickers()
            
            candidates = []
            
            for symbol, data in tickers.items():
                # Filter strictly for SPOT USDT pairs
                if not symbol.endswith('/USDT'):
                    continue
                    
                # Exclude futures/margin by symbol convention (usually handled by exchange instance, but double check)
                # Binance Spot symbols are like BTC/USDT. 
                # Avoid leveraged tokens (UP/DOWN/BEAR/BULL)
                base = symbol.split('/')[0]
                if any(x in base for x in ['UP', 'DOWN', 'BEAR', 'BULL']) or base in self.banned_coins:
                    continue
                
                # Check Volume (quoteVolume is usually in USDT)
                quote_vol = data.get('quoteVolume', 0)
                if quote_vol < min_volume_usdt:
                    continue
                    
                candidates.append({
                    'symbol': symbol,
                    'volume': quote_vol,
                    'change': data.get('percentage', 0)
                })
                
            # Sort by Volume descending
            candidates.sort(key=lambda x: x['volume'], reverse=True)
            
            top_pairs = [c['symbol'] for c in candidates[:limit]]
            logger.info(f"Top {limit} Pairs by Volume: {top_pairs}")
            return top_pairs
            
        except Exception as e:
            logger.error(f"Error selecting pairs: {e}")
            return ['BTC/USDT'] # Fallback
