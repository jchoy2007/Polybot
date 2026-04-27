import logging
import time
import feedparser
import aiohttp
from typing import Optional, Dict, List
from datetime import datetime, timezone, timedelta
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.politics")

GAMMA_API_URL = "https://gamma-api.polymarket.com"

POLITICAL_FEEDS = [
    "https://feeds.reuters.com/Reuters/worldNews",
    "https://feeds.reuters.com/reuters/topNews",
]

POLITICAL_KW = [
    "diplomatic", "ceasefire", "sanctions", "summit",
    "meeting", "talks", "negotiations", "deal",
    "iran", "tariff", "trade war", "embargo",
    "election", "vote", "congress", "senate",
    "peace", "treaty", "agreement", "nato",
]


class PoliticsTrader:
    def __init__(self):
        self.session = None
        self.last_run = 0
        self.min_interval = 900  # 15 min
        self.traded_markets = set()

    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _check_news_relevance(self, question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in POLITICAL_KW)

    async def find_political_markets(self) -> List[Dict]:
        session = await self._get_session()
        now = datetime.now(timezone.utc)
        markets = []
        for offset in [0, 100]:
            try:
                async with session.get(
                    f"{GAMMA_API_URL}/markets",
                    params={
                        "active": "true", "closed": "false",
                        "limit": 100, "offset": str(offset),
                        "end_date_min": now.isoformat(),
                        "end_date_max": (now + timedelta(days=7)).isoformat(),
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if not data:
                            break
                        for m in data:
                            q = (m.get("question") or "").lower()
                            if self._check_news_relevance(q):
                                markets.append(m)
            except Exception as e:
                logger.debug(f"Politics scan error: {e}")
        return markets

    async def run_cycle(self) -> Optional[Dict]:
        if STATE.is_paused:
            return None
        now = time.time()
        if now - self.last_run < self.min_interval:
            return None
        self.last_run = now

        logger.info("🏛️ Politics: Escaneando mercados...")
        markets = await self.find_political_markets()
        if not markets:
            logger.info("   🏛️ Sin mercados políticos activos")
            return None

        logger.info(f"   🏛️ {len(markets)} mercados políticos encontrados")

        # Por ahora solo loguear, no apostar automáticamente
        # hasta que tengamos más data de qué funciona
        for m in markets[:5]:
            q = m.get("question", "")[:60]
            logger.info(f"   🏛️ {q}")

        return None  # Solo monitoring por ahora
