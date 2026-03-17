import requests
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

class PolymarketClient:
    BASE_URL = "https://gamma-api.polymarket.com/markets"

    def __init__(self, timeout: int = 10, retries: int = 2):
        self.timeout = timeout
        self.retries = retries

    def get_probability(self, market_id: str) -> Optional[float]:
        """
        Fetch the current price (probability) for a specific market ID (clobTokenId or conditionId or slug).
        We will assume 'market_id' allows us to find the specific outcome token price.
        
        Note: Polymarket API is complex. We'll use the /markets endpoint with 'id' parameter if it's an ID, 
        or assume the user provides a 'condition_id' or we search by query.
        
        To keep it simple as per requirements: "fetch probability/price".
        We'll assume the configuration provides a Polymarket Market ID (integer or string ID).
        API usage: GET https://gamma-api.polymarket.com/markets/{id}
        """
        url = f"{self.BASE_URL}/{market_id}"
        
        for attempt in range(self.retries + 1):
            try:
                response = requests.get(url, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                
                # Data structure usually: { "id": "...", "price": 0.55, "bestBid": ..., "bestAsk": ... }
                # Or sometimes it's a list if filtering.
                # If accessed by ID directly, it should be a dict.
                
                # We expect a 'price' or 'lastTradePrice' field representing probability (0-1).
                # Sometimes raw 'price' is not there, check 'bestAsk'/'bestBid' mid?
                # Polymarket CLOB usually returns 'mid' or specific outcome prices.
                
                # Let's try to extract price safely
                price = None
                if isinstance(data, dict):
                    # Direct lookup
                    if 'price' in data:
                        price = float(data['price'])
                    elif 'bestAsk' in data and 'bestBid' in data:
                        # Mid price
                        try:
                            bid = float(data['bestBid'])
                            ask = float(data['bestAsk'])
                            if bid > 0 and ask > 0:
                                price = (bid + ask) / 2.0
                        except:
                            pass
                
                if price is not None:
                    return price
                
                logger.warning(f"Polymarket: Could not extract price for {market_id}")
                return None

            except requests.exceptions.RequestException as e:
                if attempt < self.retries:
                    logger.warning(f"Polymarket fetch failed (attempt {attempt+1}/{self.retries+1}): {e}")
                    continue
                else:
                    logger.error(f"Polymarket fetch error for {market_id}: {e}")
                    return None
            except Exception as e:
                logger.error(f"Polymarket unhandled error: {e}")
                return None
        
        return None
