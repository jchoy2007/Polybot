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
        self.cache_ttl = 600  # Re-analizar mercados cada 10 minutos

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
        Prompt calibrado basado en análisis de 14,000 wallets ganadoras.
        Usa el framework de @LunarResearcher: EV > 5% o SKIP.
        """
        return f"""You are a calibrated prediction market analyst. Your bankroll depends on accuracy.

STRICT RULES (violating any = SKIP):
1. NEVER bet on underdogs (prob < 40%). They almost always lose. SKIP.
2. If unsure, SKIP. The market is usually right. Only bet when you have STRONG evidence.
3. Penalize extreme confidence. If you say 70%, ~7/10 such calls must resolve YES.
4. Consider base rates. Most events DON'T happen. Most underdogs DON'T win.
5. Sports: home teams have ~55% base rate. Don't overestimate visitors or underdogs.
6. Never estimate above 0.90 or below 0.10 unless resolution is imminent and certain.
7. If the market already prices it correctly (within 5%), SKIP.
8. Prefer the FAVORITE side (higher probability) - it wins more often.

MARKET:
- Question: {market.question}
- Description: {market.description[:300] if market.description else 'N/A'}
- YES price: ${(market.outcome_yes_price or 0.5):.3f} (market implies {(market.outcome_yes_price or 0.5):.1%})
- NO price: ${(market.outcome_no_price or 0.5):.3f}
- Resolves in: {market.days_until_resolution or 0} day(s)
- Category: {market.category}

THE ONLY FORMULA THAT MATTERS:
EV = P_true * (1 - P_market) - (1 - P_true) * P_market
If EV < 0.05 → SKIP. No exceptions.

DECISION PROCESS:
1. Estimate TRUE probability based on evidence you're confident about
2. Calculate EV. If < 5% → SKIP immediately
3. Which side has edge? Only bet on the side where YOUR estimate > market price
4. If your estimate is within 5% of market → SKIP (market is efficient)
5. Default to SKIP when uncertain. 8 out of 10 markets should be SKIP.

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
        Analiza múltiples mercados con deduplicación y cache.
        No re-analiza mercados que ya revisó en la última hora.
        """
        import time as _time
        now = _time.time()

        # Limpiar cache viejo
        self.recently_analyzed = {
            k: v for k, v in self.recently_analyzed.items()
            if now - v < self.cache_ttl
        }

        # Deduplicar por nombre de mercado (evita analizar YES y NO del mismo)
        seen_names = set()
        unique_markets = []
        for m in markets:
            # Normalizar nombre para dedup
            name_key = m.question.lower().strip()[:50]
            if name_key in seen_names:
                continue
            seen_names.add(name_key)

            # Verificar si ya se analizó recientemente
            if name_key in self.recently_analyzed:
                logger.debug(f"   ⏭️ Saltando (analizado hace {(now - self.recently_analyzed[name_key])/60:.0f}m): {m.question[:40]}")
                continue

            unique_markets.append(m)

        if not unique_markets:
            logger.info("   No hay mercados nuevos para analizar (todos en cache)")
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

            # Marcar como analizado
            name_key = market.question.lower().strip()[:50]
            self.recently_analyzed[name_key] = now

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
