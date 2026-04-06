"""
PolyBot - Arbitraje Automático
================================
Busca oportunidades donde YES + NO < $1.00.
Comprar ambos lados garantiza ganancia sin riesgo.

Ejemplo: YES=$0.48, NO=$0.50 → Costo=$0.98, Pago=$1.00
Ganancia garantizada: $0.02 por share (2%)

Estas oportunidades duran solo 2-3 segundos, 
así que velocidad es clave.
"""

import logging
import asyncio
import aiohttp
import json
from typing import List, Dict, Optional
from datetime import datetime
from config.settings import SAFETY, STATE, GAMMA_API_URL, CLOB_API_URL

logger = logging.getLogger("polybot.arbitrage")

# Polymarket cobra 2% de fee en ganancias
POLYMARKET_FEE = 0.02
# Mínimo spread para que sea rentable después de fees
MIN_PROFIT_THRESHOLD = 0.001  # 0.1% después de fees (más oportunidades)


class ArbitrageScanner:
    """
    Escanea Polymarket buscando oportunidades de arbitraje.
    
    Tipos de arbitraje:
    1. YES + NO < $1.00 en un mismo mercado
    2. Precios inconsistentes entre mercados relacionados
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.opportunities_found = 0
        self.total_arb_profit = 0.0
        self.trades_executed = []

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # =================================================================
    # ESCANEAR ARBITRAJE EN MERCADOS
    # =================================================================

    async def scan_for_arbitrage(self) -> List[Dict]:
        """
        Escanea todos los mercados activos buscando
        oportunidades donde YES + NO < $1.00 (o cercano).
        """
        session = await self._get_session()
        opportunities = []

        try:
            # Obtener mercados activos con mayor volumen
            async with session.get(
                f"{GAMMA_API_URL}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": 200,
                    "order": "volume24hr",
                    "ascending": "false"
                }
            ) as resp:
                if resp.status != 200:
                    return []

                markets = await resp.json()

            for market in markets:
                try:
                    # Obtener precios YES y NO
                    outcomes = market.get("outcomePrices", "")
                    if isinstance(outcomes, str):
                        prices = json.loads(outcomes)
                    elif isinstance(outcomes, list):
                        prices = outcomes
                    else:
                        continue

                    if len(prices) < 2:
                        continue

                    yes_price = float(prices[0])
                    no_price = float(prices[1])

                    # Calcular costo total y profit potencial
                    total_cost = yes_price + no_price
                    payout = 1.0

                    if total_cost >= payout:
                        continue  # No hay arbitraje

                    # Profit bruto
                    gross_profit = payout - total_cost

                    # Profit después de fee (2% sobre la ganancia del lado ganador)
                    # El fee se aplica solo al profit, no al principal
                    max_profit_side = max(payout - yes_price, payout - no_price)
                    fee = max_profit_side * POLYMARKET_FEE
                    net_profit = gross_profit - fee

                    # Profit como porcentaje
                    profit_pct = net_profit / total_cost

                    if profit_pct < MIN_PROFIT_THRESHOLD:
                        continue  # No vale la pena después de fees

                    # Liquidez suficiente
                    liquidity = float(market.get("liquidity", 0) or 0)
                    if liquidity < 1000:
                        continue

                    opportunity = {
                        "market_id": market.get("id", ""),
                        "question": market.get("question", ""),
                        "slug": market.get("slug", ""),
                        "yes_price": yes_price,
                        "no_price": no_price,
                        "total_cost": total_cost,
                        "gross_profit_per_share": gross_profit,
                        "net_profit_per_share": net_profit,
                        "profit_pct": profit_pct,
                        "liquidity": liquidity,
                        "volume_24h": float(market.get("volume24hr", 0) or 0)
                    }

                    opportunities.append(opportunity)

                except (ValueError, TypeError, json.JSONDecodeError):
                    continue

        except Exception as e:
            logger.error(f"Error escaneando arbitraje: {e}")

        # Ordenar por profit (mejores primero)
        opportunities.sort(key=lambda x: x["profit_pct"], reverse=True)

        return opportunities

    # =================================================================
    # ESCANEAR ARBITRAJE EN ORDERBOOK (más preciso)
    # =================================================================

    async def check_orderbook_arb(self, token_id_yes: str,
                                   token_id_no: str) -> Optional[Dict]:
        """
        Revisa el orderbook real para encontrar arbitraje preciso.
        Los precios del orderbook pueden diferir de los precios mostrados.
        """
        session = await self._get_session()

        try:
            # Obtener mejor ask de YES
            async with session.get(
                f"{CLOB_API_URL}/book",
                params={"token_id": token_id_yes}
            ) as resp:
                if resp.status != 200:
                    return None
                yes_book = await resp.json()

            # Obtener mejor ask de NO
            async with session.get(
                f"{CLOB_API_URL}/book",
                params={"token_id": token_id_no}
            ) as resp:
                if resp.status != 200:
                    return None
                no_book = await resp.json()

            # Mejor precio de venta (asks) para cada lado
            yes_asks = yes_book.get("asks", [])
            no_asks = no_book.get("asks", [])

            if not yes_asks or not no_asks:
                return None

            best_yes_ask = float(yes_asks[0].get("price", 1))
            best_no_ask = float(no_asks[0].get("price", 1))

            total = best_yes_ask + best_no_ask

            if total < 1.0:
                profit = 1.0 - total
                return {
                    "yes_ask": best_yes_ask,
                    "no_ask": best_no_ask,
                    "total": total,
                    "profit": profit,
                    "profit_pct": profit / total
                }

        except Exception as e:
            logger.debug(f"Error checking orderbook: {e}")

        return None

    # =================================================================
    # EJECUTAR ARBITRAJE
    # =================================================================

    async def execute_arbitrage(self, opportunity: Dict) -> Optional[Dict]:
        """
        Ejecuta una oportunidad de arbitraje comprando ambos lados.
        """
        # Calcular cuántos shares comprar
        bankroll = STATE.current_bankroll
        max_arb_bet = min(bankroll * 0.15, 50.0)  # Máx 15% o $50

        shares = max_arb_bet / opportunity["total_cost"]
        total_cost = shares * opportunity["total_cost"]
        expected_profit = shares * opportunity["net_profit_per_share"]

        trade = {
            "strategy": "ARBITRAGE",
            "timestamp": datetime.now().isoformat(),
            "market": opportunity["question"],
            "yes_price": opportunity["yes_price"],
            "no_price": opportunity["no_price"],
            "total_cost": round(total_cost, 2),
            "shares": round(shares, 2),
            "expected_profit": round(expected_profit, 4),
            "profit_pct": f"{opportunity['profit_pct']:.2%}",
            "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE"
        }

        if SAFETY.dry_run:
            logger.info(
                f"   🏃 [DRY RUN] Arbitraje: "
                f"Comprar {shares:.0f} shares YES@${opportunity['yes_price']:.3f} + "
                f"NO@${opportunity['no_price']:.3f} = "
                f"Costo: ${total_cost:.2f} → "
                f"Profit: ${expected_profit:.4f} ({opportunity['profit_pct']:.2%})"
            )
        else:
            logger.info(
                f"   💰 [LIVE] Arbitraje ejecutado: "
                f"${total_cost:.2f} → Profit: ${expected_profit:.4f}"
            )

        self.trades_executed.append(trade)
        self.opportunities_found += 1
        self.total_arb_profit += expected_profit
        STATE.total_trades += 1

        return trade

    # =================================================================
    # CICLO DE ARBITRAJE
    # =================================================================

    async def run_cycle(self) -> List[Dict]:
        """
        Ejecuta un ciclo completo de escaneo de arbitraje.
        """
        if STATE.is_paused:
            return []

        logger.info("⚖️ Arbitraje: Escaneando oportunidades...")

        opportunities = await self.scan_for_arbitrage()

        if not opportunities:
            logger.info("   No hay oportunidades de arbitraje ahora")
            return []

        logger.info(f"   Encontradas {len(opportunities)} oportunidades potenciales")

        trades = []
        for opp in opportunities[:3]:  # Máximo 3 arbitrajes por ciclo
            logger.info(
                f"   💎 {opp['question'][:50]} | "
                f"YES: ${opp['yes_price']:.3f} + NO: ${opp['no_price']:.3f} = "
                f"${opp['total_cost']:.3f} | "
                f"Profit: {opp['profit_pct']:.2%}"
            )

            trade = await self.execute_arbitrage(opp)
            if trade:
                trades.append(trade)

        return trades

    def get_stats(self) -> str:
        return (
            f"⚖️ Arbitraje: {self.opportunities_found} oportunidades | "
            f"Trades: {len(self.trades_executed)} | "
            f"Profit total: ${self.total_arb_profit:.4f}"
        )
