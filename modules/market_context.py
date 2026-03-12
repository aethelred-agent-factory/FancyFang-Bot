import datetime
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import praw
import requests

try:
    import core.phemex_common as pc
except ImportError:
    # Handle if called from a script or test
    import sys
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    import core.phemex_common as pc

logger = logging.getLogger("market_context")

class MarketContext:
    """
    Manages external data sources and provides snapshots of market context.
    Each data source is cached with a TTL to avoid blocking the scan loop.
    """

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._reddit: Optional[praw.Reddit] = None
        
        # Default TTLs (seconds)
        self.ttls = {
            "fear_greed": 900,  # 15 min
            "coingecko_global": 600,  # 10 min
            "cryptopanic": 900,  # 15 min
            "reddit": 1800,  # 30 min
            "btc_momentum": 300,  # 5 min
        }
        self._init_reddit()

    def _init_reddit(self):
        """Initializes the PRAW instance if credentials are available."""
        if (
            pc.REDDIT_CLIENT_ID
            and pc.REDDIT_CLIENT_SECRET
            and pc.REDDIT_USER_AGENT
            and not self._reddit
        ):
            try:
                self._reddit = praw.Reddit(
                    client_id=pc.REDDIT_CLIENT_ID,
                    client_secret=pc.REDDIT_CLIENT_SECRET,
                    user_agent=pc.REDDIT_USER_AGENT,
                    read_only=True,
                )
                logger.info("Reddit (PRAW) initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize Reddit (PRAW): {e}")

    def _get_from_cache(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry and time.time() - entry["timestamp"] < self.ttls.get(key, 300):
                return entry["value"]
        return None

    def _set_to_cache(self, key: str, value: Any):
        with self._lock:
            self._cache[key] = {
                "timestamp": time.time(),
                "value": value
            }

    def fetch_fear_greed(self) -> Optional[int]:
        """Fetch Fear & Greed Index from alternative.me."""
        cached = self._get_from_cache("fear_greed")
        if cached is not None:
            return cached

        try:
            resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                value = int(data["data"][0]["value"])
                self._set_to_cache("fear_greed", value)
                return value
        except Exception as e:
            logger.error(f"Error fetching Fear & Greed: {e}")
        
        return self._get_from_cache("fear_greed") # Return stale if fetch fails

    def fetch_coingecko_global(self) -> Dict[str, Any]:
        """Fetch global crypto data from CoinGecko."""
        cached = self._get_from_cache("coingecko_global")
        if cached is not None:
            return cached

        try:
            resp = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
            if resp.status_code == 200:
                data = resp.json()["data"]
                res = {
                    "btc_dominance": data.get("market_cap_percentage", {}).get("btc", 0.0),
                    "total_market_cap": data.get("total_market_cap", {}).get("usd", 0.0),
                    "market_cap_change_24h": data.get("market_cap_change_percentage_24h_usd", 0.0)
                }
                self._set_to_cache("coingecko_global", res)
                return res
        except Exception as e:
            logger.error(f"Error fetching CoinGecko Global: {e}")
            
        return self._get_from_cache("coingecko_global") or {
            "btc_dominance": 0.0,
            "total_market_cap": 0.0,
            "market_cap_change_24h": 0.0
        }

    def fetch_cryptopanic_important(self) -> List[str]:
        """Fetch important headlines from CryptoPanic."""
        cached = self._get_from_cache("cryptopanic")
        if cached is not None:
            return cached

        api_key = pc.CRYPTOPANIC_API_KEY
        if not api_key:
            return []

        try:
            # Using the 'important' filter as requested
            url = f"https://cryptopanic.com/api/v1/posts/?auth_token={api_key}&filter=important"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                headlines = [post.get("title", "") for post in data.get("results", [])[:10]]
                self._set_to_cache("cryptopanic", headlines)
                return headlines
        except Exception as e:
            logger.error(f"Error fetching CryptoPanic headlines: {e}")
            
        return self._get_from_cache("cryptopanic") or []

    def fetch_reddit_hot_posts(
        self,
        subreddits: List[str] = [
            "CryptoCurrency",
            "ethtrader",
            "SatoshiStreetBets",
        ],
    ) -> List[Dict[str, Any]]:
        """Fetch top 10 hot posts from a list of subreddits."""
        cached = self._get_from_cache("reddit")
        if cached is not None:
            return cached
        if not self._reddit:
            return []

        all_posts = []
        try:
            for sub_name in subreddits:
                subreddit = self._reddit.subreddit(sub_name)
                hot_posts = subreddit.hot(limit=10)
                for post in hot_posts:
                    all_posts.append(
                        {
                            "title": post.title,
                            "score": post.score,
                            "subreddit": sub_name,
                            "url": post.url,
                        }
                    )
            # Sort by score descending and take top 10 overall
            top_posts = sorted(all_posts, key=lambda x: x["score"], reverse=True)[:10]
            self._set_to_cache("reddit", top_posts)
            return top_posts
        except Exception as e:
            logger.error(f"Error fetching Reddit posts: {e}")

        return self._get_from_cache("reddit") or []

    def get_btc_momentum(self, timeframe: str) -> float:
        """Calculate BTC momentum (percentage change) for a given timeframe."""
        key = f"btc_momentum_{timeframe}"
        cached = self._get_from_cache(key)
        if cached is not None:
            return cached

        try:
            # Get last 10 candles to calculate momentum
            candles = pc.get_candles("BTCUSDT", timeframe=timeframe, limit=11)
            if len(candles) >= 2:
                # Momentum over last 10 candles
                # Candle: [ts, interval, open, high, low, close, volume, ...]
                # index 6 is close price
                current_price = float(candles[-1][6])
                prev_price = float(candles[0][6])
                momentum = pc.pct_change(current_price, prev_price)
                self._set_to_cache(key, momentum)
                return momentum
        except Exception as e:
            logger.error(f"Error calculating BTC momentum ({timeframe}): {e}")
            
        return self._get_from_cache(key) or 0.0

    def get_market_context_snapshot(self) -> Dict[str, Any]:
        """
        Captures a snapshot of the current market context.
        Returns a dict with BTC momentum, global stats, fear/greed, etc.
        """
        cg_global = self.fetch_coingecko_global()
        
        # Calculate regime and entropy using BTC as a proxy for the global market
        # or use the last symbol scanned. Here we use BTC 1H for global regime.
        regime = "UNKNOWN"
        entropy = 0.0
        try:
            btc_candles_1h = pc.get_candles("BTCUSDT", timeframe="1H", limit=30)
            if btc_candles_1h:
                closes = [float(c[6]) for c in btc_candles_1h]
                regime, entropy = pc.calc_market_regime(closes)
        except Exception as e:
            logger.error(f"Error calculating global regime: {e}")

        snapshot = {
            "btc_momentum_4h": self.get_btc_momentum("4H"),
            "btc_momentum_1h": self.get_btc_momentum("1H"),
            "btc_momentum_15m": self.get_btc_momentum("15m"),
            "global_entropy": entropy,
            "regime": regime,
            "fear_greed_index": self.fetch_fear_greed(),
            "btc_dominance": cg_global.get("btc_dominance", 0.0),
            "total_market_cap_change_24h": cg_global.get("market_cap_change_24h", 0.0),
            "cryptopanic_headlines": self.fetch_cryptopanic_important(),
            "reddit_hot_posts": self.fetch_reddit_hot_posts(),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        
        return snapshot

# Global instance
market_ctx_manager = MarketContext()

def get_market_context_snapshot() -> Dict[str, Any]:
    return market_ctx_manager.get_market_context_snapshot()
