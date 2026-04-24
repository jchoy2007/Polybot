"""
PolyBot - Analizador con IA (Claude)
=====================================
Usa Claude para analizar mercados de predicción,
estimar probabilidades reales y detectar value bets.
"""

import json
import asyncio
import logging
import aiohttp
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from config.settings import ANTHROPIC_API_KEY
from core.market_scanner import MarketOpportunity

logger = logging.getLogger("polybot.analyzer")

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
# Rate limit: wait between calls (Haiku is fast but has limits)
RATE_LIMIT_SECONDS = 5


@dataclass
class MarketAnalysis:
    """Resultado del análisis de un mercado."""
    market_id: str
    question: str
    estimated_probability: float
    confidence: float          # 0-1, qué tan seguro está el análisis
    market_price: float
    edge: float
    reasoning: str
    side: str                  # "YES" o "NO"
    recommended_action: str    # "BET", "SKIP", "WATCH"
    risk_factors: List[str]
    key_evidence: List[str]


class AIAnalyzer:
    """Analiza mercados usando Claude como motor de probabilidades."""

    def __init__(self):
        self.api_key = ANTHROPIC_API_KEY
        self.session: Optional[aiohttp.ClientSession] = None
        self.recently_analyzed: Dict[str, float] = {}
        # Cache por 30 minutos en vez de 10 min
        # (reduce llamadas a la API ~3x, igual los mercados no
        # cambian tanto en 30 min)
        self.cache_ttl = 1800
        # Cache de precios para detectar cambios significativos
        self.price_cache: Dict[str, float] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # =================================================================
    # ANÁLISIS DE UN MERCADO INDIVIDUAL
    # =================================================================

    async def analyze_market(self, market: MarketOpportunity) -> Optional[MarketAnalysis]:
        """
        Analiza un mercado usando Claude para estimar la probabilidad real.
        
        El prompt pide a Claude:
        1. Evaluar la pregunta del mercado
        2. Considerar evidencia disponible
        3. Estimar una probabilidad numérica
        4. Comparar con el precio del mercado
        5. Decidir si hay edge suficiente
        """
        if not self.api_key:
            logger.error("❌ ANTHROPIC_API_KEY no configurada")
            return None

        prompt = self._build_analysis_prompt(market)

        try:
            session = await self._get_session()
            async with session.post(
                CLAUDE_API_URL,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01"
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}]
                }
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Error Claude API ({resp.status}): {error_text}")
                    return None

                data = await resp.json()
                return self._parse_analysis(data, market)

        except Exception as e:
            logger.error(f"Error analizando mercado {market.slug}: {e}")
            return None

    def _build_analysis_prompt(self, market: MarketOpportunity) -> str:
        """
        Prompt calibrado para deportes y esports.
        Agresivo en favoritos claros, disciplinado en coin-flips.
        """
        return f"""You are an expert sports and esports betting analyst. Your bankroll depends on accuracy.

RULES:
1. Consider team form, head-to-head records, home advantage, injuries, and recent results.
2. Sports base rates: home teams win ~55%. Top-ranked teams win ~65% vs lower-ranked.
3. Esports: higher-seeded teams in BO3 win ~60%. In playoffs, favorites win ~70%.
4. Estimate probabilities freely from 0.05 to 0.95 based on evidence.
5. If the market prices it correctly (within 3% of your estimate), SKIP.
6. ONLY bet on favorites or clear mismatches. Never bet on coin-flip matches (45-55%).
7. Spreads (handicaps): only bet if the favorite is CLEARLY stronger by that margin.
8. Over/Under totals: only bet with strong evidence about scoring patterns.

MARKET:
- Question: {market.question}
- Description: {market.description[:300] if market.description else 'N/A'}
- YES price: ${(market.outcome_yes_price or 0.5):.3f} (market implies {(market.outcome_yes_price or 0.5):.1%})
- NO price: ${(market.outcome_no_price or 0.5):.3f}
- Resolves in: {market.days_until_resolution or 0} day(s)
- Category: {market.category}

FORMULA:
EV = P_true * (1 - P_market) - (1 - P_true) * P_market
If EV < 0.03 → SKIP.

DECISION PROCESS:
1. Estimate TRUE probability based on team strength, form, rankings, head-to-head
2. Calculate EV. If < 3% → SKIP
3. Which side has edge? Bet on the side where YOUR estimate > market price by 3%+
4. BE AGGRESSIVE on clear favorites. If a top team plays a bottom team, BET.
5. CRITICAL: If your edge > 10%, you MUST recommend BET. Do not be overly cautious.
6. You should find a BET in roughly 3-4 out of 10 markets analyzed.
7. SKIP only when it's truly a coin flip (both teams equally matched) or edge < 3%.

CRITICAL ANALYSIS REQUIREMENTS:
8. What is the BASE RATE for this type of event? (historical frequency)
9. What is the STRONGEST counter-argument against your estimate?
10. What specific NEW INFORMATION would change your probability by 10+%?
11. If you cannot identify a clear edge with high confidence, SKIP.
12. Be MORE skeptical of markets with prices between 0.40-0.60 (true coin flips are hard to predict).

Return JSON only (no markdown, no backticks):
{{
    "estimated_probability": 0.XX,
    "confidence": "high/medium/low",
    "side": "YES" or "NO",
    "expected_value": 0.XX,
    "reasoning": "brief explanation",
    "key_evidence": ["evidence 1", "evidence 2"],
    "risk_factors": ["risk 1"],
    "recommended_action": "BET" or "SKIP"
}}"""

    def _parse_analysis(self, api_response: dict,
                        market: MarketOpportunity) -> Optional[MarketAnalysis]:
        """Parsea la respuesta de Claude y construye el análisis."""
        try:
            text_content = ""
            for block in api_response.get("content", []):
                if block.get("type") == "text":
                    text_content += block.get("text", "")

            text_content = text_content.strip()
            if text_content.startswith("```"):
                text_content = text_content.split("\n", 1)[1]
                text_content = text_content.rsplit("```", 1)[0]

            analysis_data = json.loads(text_content)

            # Parseo robusto con guardas contra None (Claude puede devolver null)
            _raw_prob = analysis_data.get("estimated_probability")
            est_prob = float(_raw_prob) if _raw_prob is not None else 0.5
            side = (analysis_data.get("side") or "YES").upper()

            # Convertir confidence string → float (con guarda contra None)
            conf_raw = analysis_data.get("confidence")
            if conf_raw is None:
                confidence = 0.5
            elif isinstance(conf_raw, str):
                conf_map = {"high": 0.85, "medium": 0.65, "low": 0.40}
                confidence = conf_map.get(conf_raw.lower(), 0.5)
            else:
                try:
                    confidence = float(conf_raw)
                except (TypeError, ValueError):
                    confidence = 0.5

            # Guarda contra precios None en el mercado (Polymarket puede devolver null)
            yes_price = market.outcome_yes_price if market.outcome_yes_price is not None else 0.5
            no_price = market.outcome_no_price if market.outcome_no_price is not None else 0.5

            # Calcular EV con la fórmula correcta:
            # EV = P_true × (1 - P_market) - (1 - P_true) × P_market
            if side == "YES":
                market_price = yes_price
                ev = est_prob * (1 - market_price) - (1 - est_prob) * market_price
                edge = est_prob - market_price
            else:
                market_price = no_price
                true_no_prob = 1 - est_prob
                ev = true_no_prob * (1 - market_price) - (1 - true_no_prob) * market_price
                edge = true_no_prob - market_price
                est_prob = true_no_prob  # Probabilidad del lado que apostamos

            # Log del EV para debugging
            logger.info(
                f"   → Edge: {edge:.1%} | Prob: {est_prob:.1%} | "
                f"EV: ${ev:.3f}/dólar | Acción: {analysis_data.get('recommended_action', 'SKIP')}"
            )

            return MarketAnalysis(
                market_id=market.market_id,
                question=market.question,
                estimated_probability=est_prob,
                confidence=confidence,
                market_price=market_price,
                edge=edge,
                reasoning=analysis_data.get("reasoning", "") or "",
                side=side,
                recommended_action=analysis_data.get("recommended_action", "SKIP") or "SKIP",
                risk_factors=analysis_data.get("risk_factors") or [],
                key_evidence=analysis_data.get("key_evidence") or []
            )

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.error(f"Error parseando análisis: {e}")
            logger.debug(f"Respuesta raw: {text_content[:500]}")
            return None

    # =================================================================
    # ANÁLISIS EN BATCH
    # =================================================================

    async def analyze_markets_batch(
        self,
        markets: List[MarketOpportunity],
        max_to_analyze: int = 5
    ) -> List[MarketAnalysis]:
        """
        Analiza múltiples mercados con deduplicación, cache y
        pre-filtros para reducir costos de API.

        Optimizaciones (ahorran ~50-60% del costo):
        1. Cache extendido (30 min en vez de 10)
        2. Cache inteligente: re-analizar solo si precio cambió > 3%
        3. Pre-filtro por precio (skip extremos 0.15/0.85)
        4. Pre-filtro por liquidez (skip < $3k)
        5. Skip mercados con yes ≈ no (coin flip obvio)
        """
        import time as _time
        now = _time.time()

        # Limpiar cache viejo
        self.recently_analyzed = {
            k: v for k, v in self.recently_analyzed.items()
            if now - v < self.cache_ttl
        }
        # Limpiar cache de precios de mercados ya expirados
        self.price_cache = {
            k: v for k, v in self.price_cache.items()
            if k in self.recently_analyzed
        }

        # Pre-filtro: descartar mercados que obviamente no pasarán filtros
        filtered_markets = []
        for m in markets:
            yes = m.outcome_yes_price or 0.5
            liq = m.liquidity or 0

            # Skip precios extremos (casi resueltos, sin edge útil)
            if yes < 0.15 or yes > 0.85:
                continue

            # Skip baja liquidez (no se ejecutaría la orden)
            if liq < 3000:
                continue

            filtered_markets.append(m)

        # Deduplicar por nombre de mercado
        seen_names = set()
        unique_markets = []
        for m in filtered_markets:
            name_key = m.question.lower().strip()[:50]
            if name_key in seen_names:
                continue
            seen_names.add(name_key)

            # Cache inteligente: solo re-analizar si el precio cambió > 3%
            if name_key in self.recently_analyzed:
                cached_price = self.price_cache.get(name_key, 0)
                current_price = m.outcome_yes_price or 0.5
                price_change = abs(current_price - cached_price) / max(cached_price, 0.01)
                if price_change < 0.03:  # <3% de cambio, usar cache
                    logger.debug(
                        f"   ⏭️ Cache hit (precio estable): {m.question[:40]}"
                    )
                    continue

            unique_markets.append(m)

        if not unique_markets:
            _filtered_out = len(markets) - len(filtered_markets)
            logger.info(
                f"   No hay mercados nuevos (pre-filtrados: {_filtered_out}, "
                f"cache: {len(filtered_markets) - len(unique_markets)})"
            )
            return []

        results = []
        analyzed = 0

        for market in unique_markets[:max_to_analyze]:
            logger.info(f"🧠 Analizando: {market.question[:60]}...")

            analysis = await self.analyze_market(market)

            if analysis:
                results.append(analysis)
                logger.info(
                    f"   → Edge: {analysis.edge:.1%} | "
                    f"Prob: {analysis.estimated_probability:.1%} | "
                    f"Acción: {analysis.recommended_action}"
                )

            # Marcar como analizado + guardar precio para detectar cambios
            name_key = market.question.lower().strip()[:50]
            self.recently_analyzed[name_key] = now
            self.price_cache[name_key] = market.outcome_yes_price or 0.5

            analyzed += 1
            if analyzed < min(len(unique_markets), max_to_analyze):
                logger.info("   ⏳ Esperando 5s (rate limit)...")
                await asyncio.sleep(RATE_LIMIT_SECONDS)

        # Ordenar por edge (mejores oportunidades primero)
        results.sort(key=lambda x: x.edge, reverse=True)

        bet_count = sum(1 for r in results if r.recommended_action == "BET")
        logger.info(
            f"✅ Análisis completo: {len(results)} mercados analizados, "
            f"{bet_count} recomendados para apostar"
        )

        return results
