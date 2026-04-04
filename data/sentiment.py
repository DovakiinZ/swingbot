"""
Enhanced Sentiment Engine — 4 external sources combined into a single score.

Sources:
  1. Fear & Greed Index (alternative.me) — hourly
  2. CryptoPanic News API — every 15 min
  3. Santiment social volume — every 15 min
  4. LunarCrush galaxy score — every 15 min

Each source returns a sub-score. Combined total determines:
  >= +2  → BULLISH  (full size)
   0..+1 → NEUTRAL  (half size)
  <= -1  → BEARISH  (block entries)

If any source fails or has no API key → skip gracefully, score = 0.
"""
import logging
import time
from typing import Dict, Any, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Cache intervals (seconds)
FNG_CACHE_TTL = 3600       # 1 hour
NEWS_CACHE_TTL = 900       # 15 minutes
SOCIAL_CACHE_TTL = 900     # 15 minutes
LUNAR_CACHE_TTL = 900      # 15 minutes


class SentimentEngine:
    """
    Multi-source sentiment engine.
    Fetches from 4 APIs, caches results, produces a combined sentiment decision.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.fng_url = "https://api.alternative.me/fng/?limit=1"

        # Cached values
        self._fng_cache: Dict = {}
        self._fng_time: float = 0
        self._news_cache: Dict = {}
        self._news_time: float = 0
        self._social_cache: Dict = {}
        self._social_time: float = 0
        self._lunar_cache: Dict = {}
        self._lunar_time: float = 0

    # ── Source 1: Fear & Greed Index ────────────────────────────────────

    def get_fear_and_greed(self) -> Dict[str, Any]:
        """Fetch Fear & Greed Index. Cached for 1 hour."""
        now = time.time()
        if self._fng_cache and (now - self._fng_time) < FNG_CACHE_TTL:
            return self._fng_cache

        if not self.config.get('fear_greed_enabled', True):
            return {}

        try:
            resp = requests.get(self.fng_url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if 'data' in data and len(data['data']) > 0:
                item = data['data'][0]
                result = {
                    'value': int(item['value']),
                    'classification': item['value_classification'],
                    'timestamp': int(item['timestamp']),
                }
                self._fng_cache = result
                self._fng_time = now
                return result
        except Exception as e:
            logger.warning(f"[SENTIMENT] Fear&Greed unavailable: {e}")
        return self._fng_cache if self._fng_cache else {}

    def _score_fear_greed(self) -> Tuple[int, str]:
        """
        Score mapping:
          0-25  Extreme Fear  → -2 (BLOCK)
          26-45 Fear          → -1 (reduce 50%)
          46-55 Neutral       →  0
          56-75 Greed         → +1
          76-100 Extreme Greed→ -1 (reduce — overheated)
        """
        fng = self.get_fear_and_greed()
        if not fng:
            return 0, "N/A"
        val = fng.get('value', 50)
        cls = fng.get('classification', 'Unknown')
        if val <= 25:
            return -2, f"{val} ({cls})"
        elif val <= 45:
            return -1, f"{val} ({cls})"
        elif val <= 55:
            return 0, f"{val} ({cls})"
        elif val <= 75:
            return 1, f"{val} ({cls})"
        else:
            return -1, f"{val} ({cls})"

    # ── Source 2: CryptoPanic News ──────────────────────────────────────

    def _score_cryptopanic(self, symbol: str = "") -> Tuple[int, str]:
        """Fetch important news from CryptoPanic. Cached 15 min."""
        token = self.config.get('cryptopanic_token', '')
        if not token:
            return 0, "no token"

        now = time.time()
        cache_key = symbol or "general"
        if self._news_cache.get(cache_key) and (now - self._news_time) < NEWS_CACHE_TTL:
            return self._news_cache[cache_key]

        try:
            # Extract base currency from symbol (BTC/USDT → BTC)
            currencies = ""
            if symbol:
                base = symbol.split('/')[0].split(':')[0]
                currencies = f"&currencies={base}"

            url = (
                f"https://cryptopanic.com/api/v1/posts/"
                f"?auth_token={token}&filter=important&public=true{currencies}"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            score = 0
            for post in data.get('results', [])[:10]:
                votes = post.get('votes', {})
                positive = votes.get('positive', 0) + votes.get('liked', 0)
                negative = votes.get('negative', 0) + votes.get('disliked', 0)
                if positive > negative:
                    score += 1
                elif negative > positive:
                    score -= 1

            # Cap between -3 and +3
            score = max(-3, min(3, score))
            result = (score, f"{score:+d}")
            self._news_cache[cache_key] = result
            self._news_time = now
            return result

        except Exception as e:
            logger.warning(f"[SENTIMENT] CryptoPanic unavailable: {e}")
            return 0, "error"

    # ── Source 3: Santiment Social Volume ────────────────────────────────

    def _score_santiment(self, symbol: str = "") -> Tuple[int, str]:
        """Fetch social volume from Santiment. Cached 15 min."""
        token = self.config.get('santiment_token', '')
        if not token:
            return 0, "no token"

        now = time.time()
        if self._social_cache and (now - self._social_time) < SOCIAL_CACHE_TTL:
            cached = self._social_cache.get(symbol, (0, "cached"))
            return cached

        try:
            base = symbol.split('/')[0].split(':')[0].lower() if symbol else "bitcoin"
            slug = base if base != "btc" else "bitcoin"
            if base == "eth":
                slug = "ethereum"

            query = '''
            {
              getMetric(metric: "social_volume_total") {
                timeseriesData(
                  slug: "%s"
                  from: "utc_now-1h"
                  to: "utc_now"
                  interval: "1h"
                ) { value }
              }
            }
            ''' % slug

            resp = requests.post(
                "https://api.santiment.net/graphql",
                json={"query": query},
                headers={"Authorization": f"Apikey {token}"},
                timeout=10,
            )
            data = resp.json()
            ts = data.get('data', {}).get('getMetric', {}).get('timeseriesData', [])
            if ts:
                vol = ts[-1].get('value', 0)
                # Simple heuristic: social volume > 100 is notable
                score = 1 if vol > 100 else 0
                result = (score, f"vol={vol:.0f}")
            else:
                result = (0, "no data")

            self._social_cache[symbol] = result
            self._social_time = now
            return result

        except Exception as e:
            logger.warning(f"[SENTIMENT] Santiment unavailable: {e}")
            return 0, "error"

    # ── Source 4: LunarCrush Galaxy Score ────────────────────────────────

    def _score_lunarcrush(self, symbol: str = "") -> Tuple[int, str]:
        """Fetch galaxy score from LunarCrush. Cached 15 min."""
        token = self.config.get('lunarcrush_token', '')
        if not token:
            return 0, "no token"

        now = time.time()
        if self._lunar_cache.get(symbol) and (now - self._lunar_time) < LUNAR_CACHE_TTL:
            return self._lunar_cache[symbol]

        try:
            base = symbol.split('/')[0].split(':')[0] if symbol else "BTC"

            resp = requests.get(
                "https://lunarcrush.com/api4/public/coins/list/v2",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            data = resp.json()

            galaxy_score = 0
            for coin in data.get('data', []):
                if coin.get('symbol', '').upper() == base.upper():
                    galaxy_score = coin.get('galaxy_score', 0) or 0
                    break

            if galaxy_score > 70:
                score = 2
            elif galaxy_score >= 50:
                score = 1
            else:
                score = -1 if galaxy_score > 0 else 0

            result = (score, f"gs={galaxy_score:.0f}")
            self._lunar_cache[symbol] = result
            self._lunar_time = now
            return result

        except Exception as e:
            logger.warning(f"[SENTIMENT] LunarCrush unavailable: {e}")
            return 0, "error"

    # ── Combined Sentiment Score ────────────────────────────────────────

    def get_combined_sentiment(self, symbol: str = "") -> Dict[str, Any]:
        """
        Fetch all 4 sources and produce combined sentiment decision.

        Returns:
            {
                'total_score': int,
                'decision': 'BULLISH' | 'NEUTRAL' | 'BEARISH',
                'size_multiplier': float (1.0, 0.5, or 0.0),
                'fear_greed': (score, detail),
                'cryptopanic': (score, detail),
                'santiment': (score, detail),
                'lunarcrush': (score, detail),
            }
        """
        fng_score, fng_detail = self._score_fear_greed()
        cp_score, cp_detail = self._score_cryptopanic(symbol)
        sant_score, sant_detail = self._score_santiment(symbol)
        lc_score, lc_detail = self._score_lunarcrush(symbol)

        total = fng_score + cp_score + sant_score + lc_score

        if total >= 2:
            decision = "BULLISH"
            size_mult = 1.0
        elif total >= 0:
            decision = "NEUTRAL"
            size_mult = 0.5
        else:
            decision = "BEARISH"
            size_mult = 0.0

        logger.warning(
            f"[SENTIMENT] F&G: {fng_detail} ({fng_score:+d}) | "
            f"CryptoPanic: {cp_detail} ({cp_score:+d}) | "
            f"Santiment: {sant_detail} ({sant_score:+d}) | "
            f"LunarCrush: {lc_detail} ({lc_score:+d}) | "
            f"TOTAL: {total:+d} → {decision}"
        )

        return {
            'total_score': total,
            'decision': decision,
            'size_multiplier': size_mult,
            'fear_greed': (fng_score, fng_detail),
            'cryptopanic': (cp_score, cp_detail),
            'santiment': (sant_score, sant_detail),
            'lunarcrush': (lc_score, lc_detail),
        }

    # ── Backward-compatible methods ─────────────────────────────────────

    def is_market_safe(self, threshold: int = 20) -> bool:
        """Legacy method — checks Fear & Greed only."""
        fng = self.get_fear_and_greed()
        if not fng:
            return True
        value = fng.get('value', 50)
        logger.info(f"Market Sentiment: {value} ({fng.get('classification', 'Unknown')})")
        if value < threshold:
            logger.warning(f"Sentiment {value} < Threshold {threshold}. Market Unsafe.")
            return False
        return True

    def get_score(self) -> Optional[float]:
        """Return raw Fear & Greed value for ML features."""
        fng = self.get_fear_and_greed()
        return fng.get('value') if fng else None
