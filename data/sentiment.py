import requests
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class SentimentEngine:
    """
    Fetches market sentiment data.
    Primary source: Alternative.me Crypto Fear & Greed Index (Free API).
    """
    def __init__(self):
        self.fng_url = "https://api.alternative.me/fng/"
        
    def get_fear_and_greed(self) -> Dict[str, Any]:
        """
        Returns:
            {
                "value": int (0-100),
                "classification": str (e.g., "Extreme Fear"),
                "timestamp": int
            }
        """
        try:
            response = requests.get(self.fng_url)
            response.raise_for_status()
            data = response.json()
            
            if 'data' in data and len(data['data']) > 0:
                item = data['data'][0]
                return {
                    "value": int(item['value']),
                    "classification": item['value_classification'],
                    "timestamp": int(item['timestamp'])
                }
        except Exception as e:
            logger.error(f"Error fetching Fear & Greed Index: {e}")
            
        return {}

    def is_market_safe(self, threshold: int = 20) -> bool:
        """
        Returns False if Sentiment is below threshold (e.g. Extreme Fear).
        """
        sentiment = self.get_fear_and_greed()
        if not sentiment:
            return True # Fallback to allow trading if API fails
            
        value = sentiment.get('value', 50)
        logger.info(f"Market Sentiment: {value} ({sentiment.get('classification', 'Unknown')})")
        
        if value < threshold:
            logger.warning(f"Sentiment {value} < Threshold {threshold}. Market Unsafe.")
            return False
            
        return True
