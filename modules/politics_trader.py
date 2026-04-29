import json
import logging
import os
import time
import aiohttp
from typing import Optional, Dict, List
from datetime import datetime, timezone, timedelta
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.politics")

GAMMA_API_URL = "https://gamma-api.polymarket.com"

POLITICAL_KW = [
    "diplomatic", "ceasefire", "sanctions", "summit",
    "meeting", "talks", "negotiations", "deal",
    "iran", "tariff", "trade war", "embargo",
    "election", "vote", "congress", "senate",
    "peace", "treaty", "agreement", "nato",
]

# Filtros de apuesta
EXTREME_HIGH = 0.85       # YES >= 0.85 → apostar YES
EXTREME_LOW = 0.15        # YES <= 0.15 → apostar NO
ASSUMED_TRUE_PROB = 0.93  # Prob real asumida en zona extrema (mid del rango)
MIN_LIQUIDITY = 5000.0
MAX_DAYS_TO_RESOLVE = 3
MAX_DAILY_BETS = 2
COUNTER_PATH = "data/politics_daily_count.json"


class PoliticsTrader:
    def __init__(self):
        self.session = None
        self.last_run = 0
        self.min_interval = 900  # 15 min
        self.traded_markets = set()
        self.daily_count = 0
        self.daily_count_date = ""

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

    def _load_daily_count(self, today: str) -> None:
        if self.daily_count_date == today:
            return
        try:
            if os.path.exists(COUNTER_PATH):
                with open(COUNTER_PATH) as f:
                    data = json.load(f)
                if data.get("date") == today:
                    self.daily_count = int(data.get("count", 0))
                    self.daily_count_date = today
                    return
        except Exception as e:
            logger.debug(f"Politics counter load: {e}")
        self.daily_count = 0
        self.daily_count_date = today

    def increment_daily(self, today: str) -> None:
        self._load_daily_count(today)
        self.daily_count += 1
        try:
            os.makedirs(os.path.dirname(COUNTER_PATH), exist_ok=True)
            with open(COUNTER_PATH, "w") as f:
                json.dump({"date": today, "count": self.daily_count}, f)
        except Exception as e:
            logger.warning(f"No pude persistir politics counter: {e}")

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
                        "end_date_max": (now + timedelta(days=MAX_DAYS_TO_RESOLVE)).isoformat(),
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

    def _build_candidate(self, m: Dict, now_dt: datetime) -> Optional[Dict]:
        try:
            mid = str(m.get("id") or m.get("conditionId") or "")
            if not mid or mid in self.traded_markets:
                return None

            question = m.get("question", "")

            liq = float(m.get("liquidityNum") or 0)
            if liq < MIN_LIQUIDITY:
                return None

            end_str = m.get("endDate") or m.get("endDateIso") or ""
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except Exception:
                return None
            days_left = (end_dt - now_dt).total_seconds() / 86400
            if days_left < 0 or days_left > MAX_DAYS_TO_RESOLVE:
                return None

            prices_raw = m.get("outcomePrices") or "[]"
            try:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                yes_price = float(prices[0])
            except Exception:
                return None

            if yes_price >= EXTREME_HIGH:
                side = "YES"
                bet_price = yes_price
            elif yes_price <= EXTREME_LOW:
                side = "NO"
                bet_price = 1.0 - yes_price
            else:
                return None

            edge = ASSUMED_TRUE_PROB - bet_price
            if edge <= 0:
                return None

            return {
                "market_id": mid,
                "question": question,
                "side": side,
                "price": bet_price,
                "edge": edge,
                "prob": ASSUMED_TRUE_PROB,
                "end_date": end_str,
                "liquidity": liq,
            }
        except Exception as e:
            logger.debug(f"Politics candidate parse: {e}")
            return None

    async def run_cycle(self) -> List[Dict]:
        """
        Retorna lista de candidatos a apostar (vacía si nada).
        Cada item: {market_id, question, side, price, edge, prob, end_date, liquidity}
        """
        if STATE.is_paused:
            return []
        now = time.time()
        if now - self.last_run < self.min_interval:
            return []
        self.last_run = now

        today = datetime.now().strftime("%Y-%m-%d")
        self._load_daily_count(today)
        if self.daily_count >= MAX_DAILY_BETS:
            logger.info(f"   🏛️ Límite diario alcanzado ({self.daily_count}/{MAX_DAILY_BETS})")
            return []

        logger.info("🏛️ Politics: Escaneando mercados...")
        markets = await self.find_political_markets()
        if not markets:
            logger.info("   🏛️ Sin mercados políticos activos")
            return []

        logger.info(f"   🏛️ {len(markets)} mercados políticos encontrados")

        now_dt = datetime.now(timezone.utc)
        candidates = []
        for m in markets:
            c = self._build_candidate(m, now_dt)
            if c:
                candidates.append(c)

        if not candidates:
            logger.info("   🏛️ Ningún mercado en zona extrema (yes>=0.85 o yes<=0.15)")
            return []

        logger.info(f"   🏛️ {len(candidates)} candidatos en zona extrema")
        for c in candidates[:5]:
            logger.info(
                f"   🏛️ ✅ {c['question'][:55]} | {c['side']} @ {c['price']:.3f} "
                f"(edge {c['edge']:.1%})"
            )
        return candidates

    @property
    def max_daily(self) -> int:
        return MAX_DAILY_BETS
