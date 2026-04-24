"""
PolyBot - Escáner de Mercados
==============================
Escanea Polymarket vía la Gamma API para encontrar
mercados activos con suficiente liquidez y volumen.
"""

import logging
import aiohttp
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from dataclasses import dataclass
from config.settings import GAMMA_API_URL, CLOB_API_URL, SAFETY

logger = logging.getLogger("polybot.scanner")


@dataclass
class MarketOpportunity:
    """Representa un mercado con potencial de apuesta."""
    market_id: str
    condition_id: str
    question: str
    description: str
    category: str
    outcome_yes_price: float
    outcome_no_price: float
    liquidity: float
    volume: float
    volume_24h: float
    end_date: str
    token_id_yes: str
    token_id_no: str
    slug: str
    active: bool
    days_until_resolution: int = 999
    hours_until_resolution: float = 9999.0


class MarketScanner:
    """Escanea y filtra mercados de Polymarket."""

    def __init__(self):
        self.gamma_url = GAMMA_API_URL
        self.clob_url = CLOB_API_URL
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={"Accept": "application/json"}
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # =================================================================
    # OBTENER MERCADOS ACTIVOS
    # =================================================================

    async def fetch_active_markets(self, limit: int = 100,
                                    offset: int = 0) -> List[Dict]:
        """
        Obtiene mercados activos de la Gamma API.
        
        Endpoint: GET /markets?active=true&closed=false
        """
        session = await self._get_session()
        # Filtrar server-side por fecha de resolución. Sin esto el API devuelve
        # mercados ordenados por volumen — los primeros 500-1000 son futures
        # long-term (elecciones 2028, Stanley Cup, "before GTA VI"), y los
        # mercados de <48h (los únicos que podemos tradear por SAFETY.max_resolution_days)
        # quedan enterrados más allá del offset máximo que iteramos.
        now = datetime.now(timezone.utc)
        window = timedelta(days=SAFETY.max_resolution_days + 1)
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "offset": offset,
            "order": "volume",
            "ascending": "false",
            "end_date_min": now.isoformat(),
            "end_date_max": (now + window).isoformat(),
        }

        try:
            async with session.get(
                f"{self.gamma_url}/markets", params=params
            ) as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    logger.info(f"📊 Obtenidos {len(markets)} mercados activos")
                    return markets
                else:
                    logger.error(f"Error API: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Error conectando a Gamma API: {e}")
            return []

    async def fetch_market_by_slug(self, slug: str) -> Optional[Dict]:
        """Obtiene un mercado específico por su slug."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{self.gamma_url}/markets?slug={slug}"
            ) as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    return markets[0] if markets else None
                return None
        except Exception as e:
            logger.error(f"Error buscando mercado {slug}: {e}")
            return None

    async def fetch_orderbook(self, token_id: str) -> Optional[Dict]:
        """Obtiene el order book de un mercado del CLOB."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{self.clob_url}/book",
                params={"token_id": token_id}
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            logger.error(f"Error obteniendo orderbook: {e}")
            return None

    # =================================================================
    # FILTRAR MERCADOS
    # =================================================================

    def filter_markets(self, raw_markets: List[Dict]) -> List[MarketOpportunity]:
        """
        Filtra mercados según las reglas de seguridad:
        - Liquidez mínima
        - Volumen mínimo
        - Mercado activo
        - Tiene precios válidos
        """
        opportunities = []

        for m in raw_markets:
            try:
                # Extraer datos básicos
                liquidity = float(m.get("liquidity", 0) or 0)
                volume = float(m.get("volume", 0) or 0)
                active = m.get("active", False)
                closed = m.get("closed", True)

                # Filtro 1: Activo y no cerrado
                if not active or closed:
                    continue

                # Filtro 1.5: Bloquear mercados sin fecha clara de resolución
                question = (m.get("question", "") or m.get("title", "") or "").lower()
                BLOCKED_KEYWORDS = [
                    "ipo", "valuation", "spacex", "market cap",
                    "by end of", "by december", "by june", "by the end",
                    "annual", "yearly", "lifetime",
                    "impeach", "resign", "presidency",
                ]
                if any(kw in question for kw in BLOCKED_KEYWORDS):
                    continue

                # Filtro 2: Liquidez mínima
                if liquidity < SAFETY.min_market_liquidity:
                    continue

                # Filtro 3: Volumen mínimo
                if volume < SAFETY.min_market_volume:
                    continue

                # Filtro 4: Solo mercados que se resuelven PRONTO (máx N días)
                end_date_str = m.get("endDate", "")
                days_until = 999
                hours_until = 9999.0
                if end_date_str:
                    try:
                        end_date = datetime.fromisoformat(
                            end_date_str.replace("Z", "+00:00")
                        )
                        now = datetime.now(end_date.tzinfo)
                        hours_until = (end_date - now).total_seconds() / 3600
                        days_until = int(hours_until / 24)
                        if days_until > SAFETY.max_resolution_days:
                            continue  # Solo mercados dentro del límite
                        if hours_until < 0:
                            continue  # Ya expirado
                    except (ValueError, TypeError):
                        continue  # Sin fecha válida = no apostar
                else:
                    continue  # Sin fecha = no apostar (evita SpaceX, IPO, etc)

                # Extraer precios
                outcomes = m.get("outcomePrices", "")
                if isinstance(outcomes, str):
                    # Puede venir como string JSON
                    import json
                    try:
                        prices = json.loads(outcomes)
                    except:
                        continue
                elif isinstance(outcomes, list):
                    prices = outcomes
                else:
                    continue

                if len(prices) < 2:
                    continue

                yes_price = float(prices[0])
                no_price = float(prices[1])

                # Filtro 4: Precios válidos (no en extremos)
                if yes_price < 0.03 or yes_price > 0.97:
                    continue

                # Extraer tokens
                tokens_str = m.get("clobTokenIds", "")
                if isinstance(tokens_str, str):
                    try:
                        tokens = json.loads(tokens_str)
                    except:
                        tokens = ["", ""]
                elif isinstance(tokens_str, list):
                    tokens = tokens_str
                else:
                    tokens = ["", ""]

                # Determinar categoría
                category = self._extract_category(m)

                opp = MarketOpportunity(
                    market_id=m.get("id", ""),
                    condition_id=m.get("conditionId", ""),
                    question=m.get("question", "Sin pregunta"),
                    description=m.get("description", ""),
                    category=category,
                    outcome_yes_price=yes_price,
                    outcome_no_price=no_price,
                    liquidity=liquidity,
                    volume=volume,
                    volume_24h=float(m.get("volume24hr", 0) or 0),
                    end_date=m.get("endDate", ""),
                    token_id_yes=tokens[0] if len(tokens) > 0 else "",
                    token_id_no=tokens[1] if len(tokens) > 1 else "",
                    slug=m.get("slug", ""),
                    active=True,
                    days_until_resolution=days_until,
                    hours_until_resolution=hours_until
                )
                opportunities.append(opp)

            except Exception as e:
                logger.debug(f"Error parseando mercado: {e}")
                continue

        logger.info(
            f"✅ {len(opportunities)} mercados pasan los filtros "
            f"(de {len(raw_markets)} totales)"
        )
        return opportunities

    def _extract_category(self, market: dict) -> str:
        """Extrae la categoría del mercado."""
        # Intentar desde tags
        tags = market.get("tags", [])
        if tags and isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, dict):
                    return tag.get("label", "general").lower()
                elif isinstance(tag, str):
                    return tag.lower()

        # Intentar desde el campo category
        cat = market.get("category", "")
        if cat:
            return cat.lower()

        # Inferir de la pregunta
        question = market.get("question", "").lower()
        if any(w in question for w in ["election", "president", "congress", "vote"]):
            return "politics"
        if any(w in question for w in ["bitcoin", "eth", "crypto", "token"]):
            return "crypto"
        if any(w in question for w in ["gdp", "inflation", "fed", "rate", "stock"]):
            return "economics"
        if any(w in question for w in ["game", "nba", "nfl", "win", "champion"]):
            return "sports"

        return "general"

    # =================================================================
    # ESCANEO COMPLETO
    # =================================================================

    async def scan_all_markets(self) -> List[MarketOpportunity]:
        """
        Realiza un escaneo completo:
        1. Obtiene mercados activos de la API
        2. Filtra por reglas de seguridad
        3. Ordena por volumen (más líquidos primero)
        """
        logger.info("🔍 Iniciando escaneo de mercados...")

        all_markets = []
        offset = 0
        batch_size = 100

        # Obtener hasta 500 mercados (5 páginas)
        for _ in range(5):
            batch = await self.fetch_active_markets(
                limit=batch_size, offset=offset
            )
            if not batch:
                break
            all_markets.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size
            await asyncio.sleep(0.5)  # Rate limiting

        # Filtrar
        opportunities = self.filter_markets(all_markets)

        # Ordenar: primero los que se resuelven más pronto (en horas), luego por volumen
        opportunities.sort(
            key=lambda x: (x.hours_until_resolution, -x.volume)
        )

        return opportunities
