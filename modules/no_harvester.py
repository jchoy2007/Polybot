"""
PolyBot - NO Harvester Strategy
=================================
Compra el lado casi seguro (>90%) en mercados donde el resultado
ya es prácticamente cierto. Ganancia chica por trade pero
win rate >95%.

Ejemplo: "¿BTC llegará a $0 mañana?" → NO a $0.95
         Si gana (99.9% probable): +5¢ por dólar
         Con $10 × 20 trades/día = $10/día casi garantizado

Basado en la estrategia de @switchpredicts ($5 → $5.5M)
"""

import json
import logging
import aiohttp
from typing import List, Dict, Optional
from datetime import datetime, timezone
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.harvester")

GAMMA_API_URL = "https://gamma-api.polymarket.com"


class NOHarvester:
    """Cosecha ganancias pequeñas comprando el lado casi seguro."""

    def __init__(self):
        self.harvested_markets = set()  # No repetir
        self._load_harvested()

    def _load_harvested(self):
        """Carga mercados ya cosechados."""
        try:
            with open("data/bets_placed.json", "r") as f:
                data = json.load(f)
                self.harvested_markets = set(data.get("market_ids", []))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_harvested(self, market_id: str, question: str):
        """Guarda mercado cosechado."""
        import os
        try:
            os.makedirs("data", exist_ok=True)
            try:
                with open("data/bets_placed.json", "r") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {"market_ids": [], "history": []}
            if market_id not in data["market_ids"]:
                data["market_ids"].append(market_id)
                data["history"].append({
                    "market_id": market_id,
                    "question": question,
                    "timestamp": datetime.now().isoformat(),
                    "strategy": "NO_HARVEST"
                })
            with open("data/bets_placed.json", "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    async def find_harvest_opportunities(self) -> List[Dict]:
        """
        Busca mercados donde un lado está a >90% (casi seguro).
        Estos son "dinero gratis" — baja ganancia pero casi garantizada.
        """
        opportunities = []

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            # Buscar mercados activos con alto volumen
            all_markets = []
            for offset in [0, 100, 200, 300, 400]:
                try:
                    async with session.get(
                        f"{GAMMA_API_URL}/markets",
                        params={
                            "active": "true", "closed": "false",
                            "limit": 100, "offset": str(offset),
                            "order": "volume", "ascending": "false"
                        }
                    ) as resp:
                        if resp.status == 200:
                            batch = await resp.json()
                            if not batch:
                                break
                            all_markets.extend(batch)
                except Exception:
                    break

            for m in all_markets:
                try:
                    market_id = str(m.get("id", ""))
                    condition_id = m.get("conditionId", "")
                    question = m.get("question", "")
                    liquidity = float(m.get("liquidity", 0) or 0)
                    volume = float(m.get("volume", 0) or 0)

                    # Filtros básicos
                    if liquidity < 3000 or volume < 1000:
                        continue

                    # Ya cosechado?
                    if market_id in self.harvested_markets:
                        continue
                    if condition_id in self.harvested_markets:
                        continue

                    # Parsear precios
                    outcomes = m.get("outcomePrices", "[]")
                    if isinstance(outcomes, str):
                        prices = json.loads(outcomes)
                    else:
                        prices = outcomes
                    if len(prices) < 2:
                        continue

                    yes_price = float(prices[0])
                    no_price = float(prices[1])

                    # Parsear tokens
                    tokens = m.get("clobTokenIds", "[]")
                    if isinstance(tokens, str):
                        tokens = json.loads(tokens)
                    if len(tokens) < 2:
                        continue

                    # === LÓGICA DE HARVEST ===
                    # Si YES > 90% → comprar YES (resultado casi seguro SI)
                    # Si NO > 90% (YES < 10%) → comprar NO (resultado casi seguro NO)
                    harvest_side = None
                    harvest_price = 0
                    harvest_token = ""

                    if yes_price >= 0.90 and yes_price <= 0.98:
                        harvest_side = "YES"
                        harvest_price = yes_price
                        harvest_token = tokens[0]
                        profit_per_dollar = 1.0 - yes_price
                    elif no_price >= 0.90 and no_price <= 0.98:
                        harvest_side = "NO"
                        harvest_price = no_price
                        harvest_token = tokens[1]
                        profit_per_dollar = 1.0 - no_price
                    else:
                        continue

                    # Profit mínimo 2% (no comprar a 99¢)
                    if profit_per_dollar < 0.02:
                        continue

                    # Verificar que resuelve en MÁXIMO 2 DÍAS
                    end_date_str = m.get("endDate", "")
                    days_until = 999
                    hours_until = 9999
                    if end_date_str:
                        try:
                            end_dt = datetime.fromisoformat(
                                end_date_str.replace("Z", "+00:00"))
                            delta = end_dt - datetime.now(timezone.utc)
                            days_until = delta.days
                            hours_until = delta.total_seconds() / 3600
                        except:
                            pass

                    # ESTRICTO: Solo mercados que resuelven entre HOY y 2 días
                    if days_until < 0 or days_until > 2:
                        continue

                    # Sin endDate = skip
                    if days_until == 999:
                        continue

                    # Filtrar mercados crypto up/down (esos son 50/50, no harvester)
                    q_lower = question.lower()
                    if "up or down" in q_lower or "up/down" in q_lower:
                        continue

                    # Bloquear mercados sin fecha clara
                    BLOCKED_KW = ["ipo", "valuation", "spacex", "market cap",
                                  "by end of", "by december", "annual", "lifetime",
                                  "impeach", "resign", "presidency"]
                    if any(kw in q_lower for kw in BLOCKED_KW):
                        continue

                    # Calcular monto — más conservador para harvester
                    bankroll = STATE.current_bankroll
                    # Apuesta más grande porque es más seguro
                    bet_amount = min(
                        bankroll * 0.06,  # 6% del bankroll
                        SAFETY.max_bet_absolute,
                        bankroll * 0.10  # Nunca > 10%
                    )
                    bet_amount = max(bet_amount, 2.0)  # Mínimo $2
                    bet_amount = round(bet_amount, 2)

                    expected_profit = round(bet_amount * profit_per_dollar, 2)

                    opportunities.append({
                        "market_id": market_id,
                        "condition_id": condition_id,
                        "question": question,
                        "side": harvest_side,
                        "price": harvest_price,
                        "token_id": harvest_token,
                        "profit_per_dollar": profit_per_dollar,
                        "bet_amount": bet_amount,
                        "expected_profit": expected_profit,
                        "days_until": days_until,
                        "hours_until": hours_until,
                        "liquidity": liquidity,
                        "neg_risk": m.get("negRisk", False),
                    })

                except Exception:
                    continue

        # Ordenar: primero los que resuelven más pronto, luego por profit
        opportunities.sort(key=lambda x: (x["days_until"], -x["profit_per_dollar"]))

        return opportunities

    async def execute_harvest(self, opportunities: List[Dict],
                               max_harvests: int = 3) -> List[Dict]:
        """Ejecuta las mejores oportunidades de harvest."""
        import os
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        if not opportunities:
            return []

        pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
        if not pk:
            return []
        pk_clean = pk[2:] if pk.startswith("0x") else pk

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=pk_clean, chain_id=137, signature_type=0
        )
        client.set_api_creds(client.create_or_derive_api_creds())

        executed = []

        for opp in opportunities[:max_harvests]:
            question = opp["question"][:45]
            side = opp["side"]
            price = opp["price"]
            amount = opp["bet_amount"]
            profit = opp["expected_profit"]
            token_id = opp["token_id"]

            logger.info(
                f"   🌾 HARVEST: {question} | "
                f"{side} ${price:.2f} | "
                f"${amount:.2f} → profit ~${profit:.2f} | "
                f"Resuelve: {opp.get('days_until', '?')}d"
            )

            if SAFETY.dry_run:
                logger.info(f"   🏃 [DRY RUN] Simulado")
                self.harvested_markets.add(opp["market_id"])
                executed.append({**opp, "status": "SIMULATED"})
                continue

            # Intentar FOK
            try:
                mo = MarketOrderArgs(
                    token_id=token_id,
                    amount=amount,
                    side=BUY
                )
                signed = client.create_market_order(mo)
                resp = client.post_order(signed, OrderType.FOK)

                if resp and isinstance(resp, dict):
                    oid = resp.get("orderID", "")
                    if (resp.get("success") or resp.get("status") == "matched") and oid:
                        logger.info(f"   ✅ Harvest ejecutado: {oid[:20]}...")
                        STATE.current_bankroll -= amount
                        self.harvested_markets.add(opp["market_id"])
                        self._save_harvested(opp["market_id"], opp["question"])
                        executed.append({**opp, "status": "EXECUTED"})
                        STATE.total_trades += 1
                        continue
            except Exception as e:
                logger.debug(f"   FOK falló: {str(e)[:50]}")

            # Intentar GTC
            try:
                limit_price = round(price + 0.01, 2)
                size = round(amount / price, 2)
                lo = OrderArgs(
                    token_id=token_id,
                    price=limit_price,
                    size=size,
                    side=BUY
                )
                signed_l = client.create_order(lo)
                resp_l = client.post_order(signed_l, OrderType.GTC)

                if resp_l and isinstance(resp_l, dict):
                    oid = resp_l.get("orderID", "")
                    if oid or resp_l.get("success"):
                        logger.info(f"   ✅ Harvest GTC: {oid[:20]}...")
                        STATE.current_bankroll -= amount
                        self.harvested_markets.add(opp["market_id"])
                        self._save_harvested(opp["market_id"], opp["question"])
                        executed.append({**opp, "status": "EXECUTED"})
                        STATE.total_trades += 1
                        continue
            except Exception as e:
                logger.debug(f"   GTC falló: {str(e)[:50]}")

            logger.info(f"   ❌ No se ejecutó")
            # Agregar a la lista para no reintentar cada ciclo
            self.harvested_markets.add(opp["market_id"])
            executed.append({**opp, "status": "FAILED"})

        return executed
