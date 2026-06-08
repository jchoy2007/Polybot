"""
ArbTrader — Arbitraje YES+NO en Polymarket
==========================================
Busca mercados donde YES + NO < $1.00 y compra ambos lados.
Ganancia garantizada al vencer sin importar el resultado.

Ejemplo:
  YES @ $0.45 + NO @ $0.50 → costo total = $0.95
  Compra 8 tokens de cada lado: $3.60 (YES) + $4.00 (NO) = $7.60
  Resolución: 8 tokens del ganador = $8.00 → profit $0.40 (5.26%)

Riesgos controlados:
  - Precio cambia entre compra YES y NO (ejecutar en <3 seg)
  - Gas Polygon: ~$0.03 por tx (negligible)
  - Mercado no resuelve: extremadamente raro en Polymarket
"""

import json
import time
import logging
import asyncio
import aiohttp
from typing import Optional, Dict, List
from datetime import datetime, timezone
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.arb")

GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Spread mínimo para cubrir slippage potencial y gas
MIN_ARB_SPREAD = 0.04   # 4% mínimo garantizado
MAX_ARB_SPEND  = 12.0   # Máximo total ($6 por lado)
MIN_ARB_SPEND  = 3.0    # Mínimo total para que valga el gas

# Solo mercados con buena liquidez en ambos lados
MIN_LIQUIDITY  = 5000   # $5k de liquidez mínima
MIN_VOLUME     = 2000   # $2k de volumen mínimo


class ArbTrader:
    """Ejecuta arbitraje YES+NO cuando spread > 4% en Polymarket."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_run = 0
        self.min_interval = 120      # Escanear cada 2 min (más frecuente que otras estrategias)
        self.traded_markets: set = set()
        self.arb_count_today = {"date": "", "count": 0}
        self._load_traded()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _load_traded(self):
        try:
            with open("data/bets_placed.json", "r") as f:
                data = json.load(f)
                self.traded_markets = set(data.get("market_ids", []))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_arb(self, market_id: str, question: str, spread: float):
        try:
            import os
            os.makedirs("data", exist_ok=True)
            try:
                with open("data/bets_placed.json", "r") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {"market_ids": [], "history": []}
            if market_id not in data["market_ids"]:
                data["market_ids"].append(market_id)
                data["history"].append({
                    "market_id": market_id, "question": question,
                    "timestamp": datetime.now().isoformat(),
                    "strategy": "ARB", "spread": round(spread, 4)
                })
            with open("data/bets_placed.json", "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # PUNTO DE ENTRADA
    # ═══════════════════════════════════════════════════════════════

    async def run_cycle(self) -> Optional[Dict]:
        if STATE.is_paused:
            return None

        now = time.time()
        if now - self.last_run < self.min_interval:
            return None
        self.last_run = now

        # Límite diario conservador: max 3 arbs/día para no agotar capital
        today = datetime.now().strftime("%Y-%m-%d")
        if self.arb_count_today["date"] != today:
            self.arb_count_today = {"date": today, "count": 0}
        if self.arb_count_today["count"] >= 3:
            return None

        logger.info("♻️  Arb Trader: Escaneando spreads YES+NO...")

        opportunities = await self._find_arb_opportunities()
        if not opportunities:
            logger.info("   ♻️  Sin oportunidades de arbitraje en este ciclo")
            return None

        # Tomar la mejor oportunidad (mayor spread primero)
        best = opportunities[0]
        logger.info(
            f"   ♻️  Mejor arb: {best['question'][:50]} | "
            f"Spread {best['spread']:.1%} | "
            f"YES={best['yes_price']:.3f} NO={best['no_price']:.3f}"
        )

        return await self._execute_arb(best)

    # ═══════════════════════════════════════════════════════════════
    # BUSCAR OPORTUNIDADES
    # ═══════════════════════════════════════════════════════════════

    async def _find_arb_opportunities(self) -> List[Dict]:
        session = await self._get_session()
        opportunities = []

        for offset in [0, 100, 200, 300]:
            try:
                async with session.get(
                    f"{GAMMA_API_URL}/markets",
                    params={
                        "active": "true", "closed": "false",
                        "limit": 100, "offset": str(offset),
                        "order": "liquidity", "ascending": "false",
                    }
                ) as resp:
                    if resp.status != 200:
                        break
                    batch = await resp.json()
                    if not batch:
                        break

                    for m in batch:
                        opp = self._check_arb(m)
                        if opp:
                            opportunities.append(opp)

            except Exception as e:
                logger.debug(f"   ♻️  Gamma API error offset {offset}: {e}")
                break

        # Ordenar por spread descendente (mejor primero)
        opportunities.sort(key=lambda x: x["spread"], reverse=True)
        if opportunities:
            logger.info(f"   ♻️  {len(opportunities)} oportunidades de arb encontradas")
        return opportunities

    def _check_arb(self, market: Dict) -> Optional[Dict]:
        """Evalúa si un mercado tiene spread arbitrageable."""
        market_id = str(market.get("id", ""))
        cid = market.get("conditionId", "")
        if market_id in self.traded_markets or cid in self.traded_markets:
            return None

        # Liquidez mínima para poder ejecutar ambos lados
        liq = float(market.get("liquidityNum") or market.get("liquidity") or 0)
        vol = float(market.get("volumeNum") or market.get("volume") or 0)
        if liq < MIN_LIQUIDITY or vol < MIN_VOLUME:
            return None

        # Precios
        outcomes = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str):
            try:
                prices = json.loads(outcomes)
            except Exception:
                return None
        else:
            prices = outcomes

        if len(prices) < 2:
            return None

        try:
            yes_price = float(prices[0])
            no_price  = float(prices[1])
        except (ValueError, TypeError):
            return None

        # Validar precios razonables (no cerrados ni sin liquidez)
        if yes_price <= 0.02 or yes_price >= 0.98:
            return None
        if no_price <= 0.02 or no_price >= 0.98:
            return None

        spread = 1.0 - (yes_price + no_price)
        if spread < MIN_ARB_SPREAD:
            return None

        # Token IDs necesarios para comprar cada lado
        tokens = market.get("clobTokenIds", "[]")
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except Exception:
                return None
        if len(tokens) < 2:
            return None

        # Calcular cuántos tokens comprar y costo total
        # Queremos T tokens de cada lado donde costo total ≤ MAX_ARB_SPEND
        # total_cost = T * (yes_price + no_price)
        # → T = MAX_ARB_SPEND / (yes_price + no_price)
        price_sum = yes_price + no_price
        T = MAX_ARB_SPEND / price_sum
        # Redondear a 1 decimal
        T = round(T, 1)

        yes_spend = round(T * yes_price, 2)
        no_spend  = round(T * no_price,  2)
        total_spend = yes_spend + no_spend
        guaranteed_return = T   # $1.00 per token
        guaranteed_profit = guaranteed_return - total_spend

        if total_spend < MIN_ARB_SPEND:
            return None

        # Cada lado debe ser >= min_bet_size
        if yes_spend < SAFETY.min_bet_size or no_spend < SAFETY.min_bet_size:
            return None

        # Verificar resolución no muy lejana (máx 30 días — arb funciona mejor pronto)
        end_str = market.get("endDate", "")
        hours_to_end = None
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                hours_to_end = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                if hours_to_end < 0 or hours_to_end > 720:  # > 30 días → skip
                    return None
            except Exception:
                pass

        return {
            "market_id": market_id,
            "condition_id": cid,
            "question": market.get("question", ""),
            "yes_price": yes_price,
            "no_price": no_price,
            "spread": spread,
            "token_yes": tokens[0],
            "token_no": tokens[1],
            "T_tokens": T,
            "yes_spend": yes_spend,
            "no_spend": no_spend,
            "total_spend": total_spend,
            "guaranteed_profit": guaranteed_profit,
            "profit_pct": spread / price_sum,  # ROI real
            "hours_to_end": hours_to_end,
            "liquidity": liq,
        }

    # ═══════════════════════════════════════════════════════════════
    # EJECUTAR ARBITRAJE
    # ═══════════════════════════════════════════════════════════════

    async def _execute_arb(self, opp: Dict) -> Optional[Dict]:
        """Ejecuta ambos lados del arb. Si YES falla, NO se aborta."""
        trade = {
            "strategy": "ARB",
            "timestamp": datetime.now().isoformat(),
            "market_id": opp["market_id"],
            "question": opp["question"],
            "yes_price": opp["yes_price"],
            "no_price": opp["no_price"],
            "spread": opp["spread"],
            "yes_spend": opp["yes_spend"],
            "no_spend": opp["no_spend"],
            "total_spend": opp["total_spend"],
            "guaranteed_profit": opp["guaranteed_profit"],
            "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE",
        }

        if SAFETY.dry_run:
            trade["status"] = "SIMULATED"
            logger.info(
                f"      🏃 [DRY RUN] ARB: YES ${opp['yes_spend']:.2f} @ {opp['yes_price']:.3f} "
                f"+ NO ${opp['no_spend']:.2f} @ {opp['no_price']:.3f} "
                f"→ profit garantizado ${opp['guaranteed_profit']:.2f} ({opp['spread']:.1%})"
            )
            return trade

        logger.info(
            f"      💰 [LIVE] ARB: comprando YES ${opp['yes_spend']:.2f} + NO ${opp['no_spend']:.2f}"
        )

        try:
            import os
            from py_clob_client_v2.client import ClobClient
            from py_clob_client_v2.clob_types import MarketOrderArgs, OrderArgs, OrderType
            from py_clob_client_v2 import Side
            BUY = Side.BUY

            pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if not pk:
                trade["status"] = "NO_KEY"
                return trade
            pk_clean = pk[2:] if pk.startswith("0x") else pk
            sig_type = int(os.getenv("SIGNATURE_TYPE", "2"))
            funder_param = os.getenv("POLYMARKET_FUNDER_ADDRESS") if sig_type > 0 else None

            client = ClobClient(
                host="https://clob.polymarket.com",
                key=pk_clean, chain_id=137,
                signature_type=sig_type, funder=funder_param
            )
            client.set_api_creds(client.create_or_derive_api_key())

            # --- Comprar YES primero ---
            yes_executed = False
            try:
                mo_yes = MarketOrderArgs(
                    token_id=opp["token_yes"], amount=opp["yes_spend"], side=BUY
                )
                signed_yes = client.create_market_order(mo_yes)
                resp_yes = client.post_order(signed_yes, OrderType.FOK)
                if resp_yes and (resp_yes.get("success") or resp_yes.get("status") == "matched"):
                    yes_executed = True
                    logger.info(f"      ✅ YES ejecutado: {resp_yes.get('orderID','')[:20]}...")
            except Exception as e:
                logger.warning(f"      ❌ YES FOK falló: {str(e)[:80]}")

            if not yes_executed:
                trade["status"] = "YES_FAILED"
                logger.warning("      ⚠️  YES falló — abortando arb (NO no se compra)")
                return trade

            # --- Comprar NO inmediatamente después ---
            # Pequeña pausa para que el estado del CLOB se actualice
            await asyncio.sleep(0.5)

            no_executed = False
            try:
                mo_no = MarketOrderArgs(
                    token_id=opp["token_no"], amount=opp["no_spend"], side=BUY
                )
                signed_no = client.create_market_order(mo_no)
                resp_no = client.post_order(signed_no, OrderType.FOK)
                if resp_no and (resp_no.get("success") or resp_no.get("status") == "matched"):
                    no_executed = True
                    logger.info(f"      ✅ NO ejecutado: {resp_no.get('orderID','')[:20]}...")
            except Exception as e:
                logger.warning(f"      ❌ NO FOK falló: {str(e)[:80]}")

            if not no_executed:
                # YES comprado pero NO falló — quedamos expuestos al lado YES
                trade["status"] = "PARTIAL_YES_ONLY"
                logger.warning(
                    "      ⚠️  NO falló después de YES ejecutado — "
                    "posición unilateral en YES (riesgo asumido)"
                )
                STATE.current_bankroll -= opp["yes_spend"]
                self.traded_markets.add(opp["market_id"])
                self._save_arb(opp["market_id"], opp["question"], opp["spread"])
                return trade

            # --- Ambos lados ejecutados ✅ ---
            trade["status"] = "EXECUTED"
            total = opp["yes_spend"] + opp["no_spend"]
            STATE.current_bankroll -= total
            self.traded_markets.add(opp["market_id"])
            self._save_arb(opp["market_id"], opp["question"], opp["spread"])
            self.arb_count_today["count"] += 1
            STATE.total_trades += 1

            logger.info(
                f"      ✅ ARB completo! Invertido ${total:.2f} → "
                f"profit garantizado ${opp['guaranteed_profit']:.2f} "
                f"({opp['spread']:.1%}) al resolver"
            )

        except Exception as e:
            trade["status"] = "ERROR"
            trade["error"] = str(e)
            logger.error(f"      ❌ Arb error: {e}")

        return trade
