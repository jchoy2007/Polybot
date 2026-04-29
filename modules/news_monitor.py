import feedparser
import logging
import time
from typing import Dict, Optional

logger = logging.getLogger("polybot.news")

RSS_FEEDS = [
    # Reuters cerró sus RSS públicos (29-Abr verificado: 0 entries).
    # Reemplazados por feeds activos: Yahoo Finance (42), Bloomberg (30), MarketWatch (10), WSJ (20).
    "https://finance.yahoo.com/news/rssindex",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
]

BULLISH_KW = [
    "rally", "surge", "gain", "rise", "jump", "soar",
    "record high", "bull", "optimism", "recovery",
    "deal", "peace", "ceasefire", "agreement",
    "beat expectations", "strong earnings",
]

BEARISH_KW = [
    "crash", "drop", "fall", "plunge", "tumble", "sink",
    "recession", "fear", "panic", "sell-off", "selloff",
    "tariff", "war", "sanctions", "threat", "crisis",
    "miss expectations", "weak earnings", "layoffs",
]


class NewsMonitor:
    def __init__(self):
        self.cache = {"ts": 0, "score": 0, "headlines": []}
        self.cache_ttl = 900  # 15 min cache

    def get_sentiment(self) -> Dict:
        now = time.time()
        if now - self.cache["ts"] < self.cache_ttl:
            return self.cache

        bullish = 0
        bearish = 0
        headlines = []

        for feed_url in RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:10]:
                    title = entry.get("title", "").lower()
                    headlines.append(title)
                    if any(kw in title for kw in BULLISH_KW):
                        bullish += 1
                    if any(kw in title for kw in BEARISH_KW):
                        bearish += 1
            except Exception as e:
                logger.debug(f"RSS error {feed_url}: {e}")

        score = bullish - bearish
        result = {
            "ts": now,
            "score": score,
            "bullish": bullish,
            "bearish": bearish,
            "headlines": len(headlines),
            "sentiment": "BULLISH" if score > 2 else "BEARISH" if score < -2 else "NEUTRAL"
        }
        self.cache = result
        logger.info(
            f"📰 News: {result['sentiment']} "
            f"(bull:{bullish} bear:{bearish} score:{score:+d})"
        )
        return result
