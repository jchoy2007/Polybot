"""
PolyBot - Crypto Grinder 95-99¢ (Sharky6999 Strategy)
=======================================================
Compra contratos crypto de 5-min/15-min/1-hr cuando un lado
está a 95-99¢ y Binance confirma la dirección.

Inspirado en: Sharky6999 ($809K profit, 99.3% win rate, 27K trades)

Cómo funciona:
1. Escanea mercados crypto cortos (5min, 15min, 1hr up/down)
2. IGNORA todo por debajo de 95¢
3. Si un lado está a 95-99¢, verifica en Binance WebSocket
4. Si Binance confirma la dirección → compra
5. Espera resolución → cobra $1.00
6. Ganancia: 1-5¢ por dólar, 98-99% win rate

Capital recomendado: $50+ para empezar
"""

import os
import json
import time
import logging
import asyncio
import aiohttp
from typing import Optional, Dict, List
from datetime import datetime, timezone
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.grinder")

GAMMA_API_URL = "https://gamma-api.polymarket.com"
BINANCE_API = "https://api.binance.com/api/v3"

# Parámetros de la estrategia
MIN_PRICE = 0.95       # Solo comprar a 95¢ o más
MAX_PRICE = 0.99       # No comprar a 99¢+ (spread muy chico, fees comen ganancia)
MIN_CONFIRM_MOVE = 0.0005  # Binance debe confirmar movimiento de 0.05%+
MAX_MINUTES_LEFT = 8   # Solo mercados con <8 minutos para resolver
MIN_MINUTES_LEFT = 0.5 # No comprar si ya casi resuelve
KELLY_FRACTION = 0.30  # Kelly agresivo (alta probabilidad)
MAX_BET_PCT = 0.05     # Máximo 5% del bankroll por trade
COOLDOWN_SECONDS = 120 # 2 min entre trades del mismo crypto


class CryptoGrinder:
    """Compra contratos crypto casi seguros (95-99¢) para grind constante."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_trade_time: Dict[str, float] = {}  # Por mercado
        self.traded_markets: set = set()
        self._load_traded()
        self.stats = {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0}
        self.cryptos = {
            "BTC": {"symbol": "BTCUSDT", "keywords": ["btc", "bitcoin"]},
            "ETH": {"symbol": "ETHUSDT", "keywords": ["eth", "ethereum"]},
            "SOL": {"symbol": "SOLUSDT", "keywords": ["sol", "solana"]},
        }

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

    def _save_bet(self, market_id: str, question: str = ""):
        try:
            os.makedirs("data", exist_ok=True)
            try:
                with open("data/bets_placed.json", "r") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {"market_ids": [], "history": []}
            if market_id and market_id not in data["market_ids"]:
                data["market_ids"].append(market_id)
                data["history"].append({
                    "market_id": market_id, "question": question,
                    "timestamp": datetime.now().isoformat(),
                    "strategy": "GRINDER"
                })
            with open("data/bets_placed.json", "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # PUNTO DE ENTRADA
    # ═══════════════════════════════════════════════════════════════

    async def run_cycle(self) -> Optional[Dict]:
        """
        Ciclo del grinder:
        1. Busca mercados crypto cortos
        2. Filtra solo los que tienen un lado a 95-99¢
        3. Verifica con Binance
        4. Compra si confirma
        """
        if STATE.is_paused:
            return None

        logger.info("💎 Crypto Grinder: Buscando contratos 95-99¢...")

        # 1. Buscar mercados crypto de corto plazo
        markets = await self._find_crypto_short_markets()
        if not markets:
            logger.info("   💎 No hay mercados crypto cortos activos")
            return None

        logger.info(f"   💎 {len(markets)} mercados crypto cortos encontrados")

        # 2. Filtrar por precio 95-99¢
        opportunities = []
        for market in markets:
            opp = self._check_price_opportunity(market)
            if opp:
                opportunities.append(opp)

        if not opportunities:
            logger.info("   💎 Ningún contrato en rango 95-99¢")
            return None

        logger.info(f"   💎 {len(opportunities)} contratos en rango 95-99¢")

        # 3. Verificar con Binance y ejecutar
        for opp in opportunities:
            # Cooldown check
            market_id = opp["market_id"]
            if market_id in self.traded_markets:
                continue
            last = self.last_trade_time.get(opp["crypto"], 0)
            if time.time() - last < COOLDOWN_SECONDS:
                continue

            # Verificar Binance
            confirmed = await self._verify_binance(opp)
            if not confirmed:
                logger.info(f"   💎 {opp['crypto']} {opp['side']} @ {opp['price']:.2f} — Binance NO confirma")
                continue

            # Calcular bet size
            bet_amount = self._calculate_bet(opp)
            if bet_amount < SAFETY.min_bet_size:
                continue

            logger.info(
                f"   💎 GRIND: {opp['question'][:45]}\n"
                f"      {opp['side']} @ {opp['price']:.2f} (edge: {opp['edge']:.1%})\n"
                f"      Binance confirma: {opp['crypto']} {opp['binance_direction']} {opp['binance_move']:.3f}%\n"
                f"      Apuesta: ${bet_amount:.2f} | Resuelve en: {opp['minutes_left']:.1f} min"
            )

            # 4. Ejecutar
            trade = {
                "strategy": "GRINDER",
                "timestamp": datetime.now().isoformat(),
                "market_id": market_id,
                "question": opp["question"],
                "side": opp["side"],
                "amount": bet_amount,
                "price": opp["price"],
                "edge": opp["edge"],
                "crypto": opp["crypto"],
                "minutes_left": opp["minutes_left"],
                "binance_move": opp["binance_move"],
                "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE"
            }

            if SAFETY.dry_run:
                trade["status"] = "SIMULATED"
                logger.info(f"   💎 [DRY RUN] {opp['side']} ${bet_amount:.2f} @ {opp['price']:.2f}")
            else:
                logger.info(f"   💎 [LIVE] {opp['side']} ${bet_amount:.2f} @ {opp['price']:.2f}")
                try:
                    executed = await self._execute_order(
                        opp["token_id"], opp["price"], bet_amount
                    )
                    if executed:
                        trade["status"] = "EXECUTED"
                        STATE.current_bankroll -= bet_amount
                        self.traded_markets.add(market_id)
                        self._save_bet(market_id, opp["question"])
                        self.last_trade_time[opp["crypto"]] = time.time()
                        STATE.total_trades += 1
                        STATE.open_positions += 1
                        self.stats["trades"] += 1
                        logger.info(f"   💎 Ejecutado! Capital: ${STATE.current_bankroll:.2f}")
                    else:
                        trade["status"] = "FAILED"
                        self.traded_markets.add(market_id)
                except Exception as e:
                    trade["status"] = "ERROR"
                    trade["error"] = str(e)
                    logger.error(f"   💎 Error: {e}")

            return trade

        logger.info("   💎 Sin oportunidades confirmadas por Binance")
        return None

    # ═══════════════════════════════════════════════════════════════
    # BUSCAR MERCADOS CRYPTO CORTOS
    # ═══════════════════════════════════════════════════════════════

    async def _find_crypto_short_markets(self) -> List[Dict]:
        """Busca mercados crypto de 5min, 15min, 1hr."""
        session = await self._get_session()
        crypto_kw = ["btc", "bitcoin", "eth", "ethereum", "sol", "solana"]
        time_kw = ["5-min", "5 min", "15-min", "15 min", "1-hour", "1 hour",
                    "30-min", "30 min", "up or down", "up/down"]

        markets = []
        for offset in [0, 100, 200, 300, 400, 500, 600]:
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
                        for m in batch:
                            q = (m.get("question") or "").lower()
                            has_crypto = any(kw in q for kw in crypto_kw)
                            has_time = any(kw in q for kw in time_kw)
                            if not (has_crypto and has_time):
                                continue

                            # Verificar que resuelve pronto
                            end_str = m.get("endDate", "")
                            if not end_str:
                                continue
                            try:
                                if end_str.endswith("Z"):
                                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                                elif "+" in end_str[-6:]:
                                    end_dt = datetime.fromisoformat(end_str)
                                else:
                                    end_dt = datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc)
                                minutes_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 60
                                if MIN_MINUTES_LEFT < minutes_left < MAX_MINUTES_LEFT:
                                    m["_minutes_left"] = minutes_left
                                    markets.append(m)
                            except:
                                continue
            except:
                break

        # Ordenar por tiempo restante (los que cierran primero = más predecibles)
        markets.sort(key=lambda m: m.get("_minutes_left", 999))
        return markets

    # ═══════════════════════════════════════════════════════════════
    # VERIFICAR PRECIO 95-99¢
    # ═══════════════════════════════════════════════════════════════

    def _check_price_opportunity(self, market: Dict) -> Optional[Dict]:
        """Verifica si algún lado del mercado está en 95-99¢."""
        question = market.get("question", "")
        market_id = str(market.get("id", ""))

        if market_id in self.traded_markets:
            return None

        # Parsear precios
        try:
            outcomes = market.get("outcomePrices", "[]")
            if isinstance(outcomes, str):
                prices = json.loads(outcomes)
            else:
                prices = outcomes
            if len(prices) < 2:
                return None

            yes_price = float(prices[0])
            no_price = float(prices[1])
        except:
            return None

        # Parsear tokens
        try:
            tokens = market.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if len(tokens) < 2:
                return None
        except:
            return None

        # Detectar crypto
        q_lower = question.lower()
        crypto = None
        for name, info in self.cryptos.items():
            if any(kw in q_lower for kw in info["keywords"]):
                crypto = name
                break
        if not crypto:
            return None

        # Detectar si es "up or down" y cuál lado está caro
        is_up = "up" in q_lower.split("or")[0] if "or" in q_lower else "up" in q_lower

        # Verificar rango 95-99¢
        opportunity = None

        if MIN_PRICE <= yes_price <= MAX_PRICE:
            opportunity = {
                "market_id": market_id,
                "question": question,
                "crypto": crypto,
                "side": "YES",
                "price": yes_price,
                "token_id": tokens[0],
                "edge": 1.0 - yes_price,  # Edge = cuánto ganamos por dólar
                "minutes_left": market.get("_minutes_left", 0),
                "expected_direction": "UP" if is_up else "DOWN",
            }

        if MIN_PRICE <= no_price <= MAX_PRICE:
            no_opp = {
                "market_id": market_id,
                "question": question,
                "crypto": crypto,
                "side": "NO",
                "price": no_price,
                "token_id": tokens[1],
                "edge": 1.0 - no_price,
                "minutes_left": market.get("_minutes_left", 0),
                "expected_direction": "DOWN" if is_up else "UP",
            }
            # Preferir el que tiene mayor edge (precio más bajo)
            if opportunity is None or no_opp["edge"] > opportunity["edge"]:
                opportunity = no_opp

        return opportunity

    # ═══════════════════════════════════════════════════════════════
    # VERIFICAR CON BINANCE
    # ═══════════════════════════════════════════════════════════════

    async def _verify_binance(self, opp: Dict) -> bool:
        """
        Verifica que Binance confirma la dirección del mercado.
        Si el mercado dice UP a 97¢, Binance debe mostrar que el precio
        realmente está subiendo.
        """
        crypto = opp["crypto"]
        symbol = self.cryptos[crypto]["symbol"]
        expected_dir = opp["expected_direction"]

        session = await self._get_session()

        try:
            # Obtener klines de 1 minuto (últimos 5)
            async with session.get(
                f"{BINANCE_API}/klines",
                params={"symbol": symbol, "interval": "1m", "limit": 5}
            ) as resp:
                if resp.status != 200:
                    return False
                klines = await resp.json()
                if len(klines) < 3:
                    return False

            current = float(klines[-1][4])  # Close actual
            prev_3m = float(klines[-3][4])  # Close 3 min atrás
            move_pct = ((current - prev_3m) / prev_3m) * 100

            opp["binance_move"] = abs(move_pct)
            opp["binance_direction"] = "UP" if move_pct > 0 else "DOWN"

            # Verificar que la dirección coincide Y el movimiento es significativo
            if expected_dir == "UP" and move_pct > MIN_CONFIRM_MOVE * 100:
                return True
            if expected_dir == "DOWN" and move_pct < -MIN_CONFIRM_MOVE * 100:
                return True

            return False

        except Exception as e:
            logger.debug(f"   Binance check error: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════
    # CALCULAR APUESTA
    # ═══════════════════════════════════════════════════════════════

    def _calculate_bet(self, opp: Dict) -> float:
        """Calcula monto con Kelly fraccional, más agresivo que otras estrategias."""
        bankroll = STATE.current_bankroll
        edge = opp["edge"]  # 1-5% típicamente
        price = opp["price"]

        # Kelly: f* = (b*p - q) / b donde b = (1-price)/price
        b = (1 - price) / price if price > 0 else 0
        # Con 98% de probabilidad de ganar
        win_prob = 0.98
        kelly = (b * win_prob - (1 - win_prob)) / b if b > 0 else 0

        bet = bankroll * kelly * KELLY_FRACTION
        bet = max(bet, SAFETY.min_bet_size)
        bet = min(bet, bankroll * MAX_BET_PCT)
        bet = min(bet, SAFETY.max_bet_absolute)
        bet = min(bet, bankroll * 0.95)  # Nunca todo

        return round(bet, 2)

    # ═══════════════════════════════════════════════════════════════
    # EJECUCIÓN REAL
    # ═══════════════════════════════════════════════════════════════

    async def _execute_order(self, token_id: str, price: float,
                              amount: float) -> bool:
        """Ejecuta orden FOK → GTC fallback."""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if not pk:
                return False
            pk_clean = pk[2:] if pk.startswith("0x") else pk

            client = ClobClient(
                host="https://clob.polymarket.com",
                key=pk_clean, chain_id=137, signature_type=0
            )
            client.set_api_creds(client.create_or_derive_api_creds())

            # FOK (Fill-or-Kill) — ejecución inmediata
            try:
                mo = MarketOrderArgs(token_id=token_id, amount=amount, side=BUY)
                signed = client.create_market_order(mo)
                resp = client.post_order(signed, OrderType.FOK)
                if resp and isinstance(resp, dict):
                    oid = resp.get("orderID", "")
                    if (resp.get("success") or resp.get("status") == "matched") and oid:
                        logger.info(f"   💎 FOK ejecutada: {oid[:20]}...")
                        return True
            except Exception as e:
                logger.debug(f"   FOK falló: {str(e)[:60]}")

            # GTC fallback — orden límite al precio actual
            try:
                limit_price = min(price + 0.01, 0.99)
                size = round(amount / max(price, 0.01), 2)
                lo = OrderArgs(
                    token_id=token_id,
                    price=round(limit_price, 2),
                    size=size, side=BUY
                )
                signed_l = client.create_order(lo)
                resp_l = client.post_order(signed_l, OrderType.GTC)
                if resp_l and isinstance(resp_l, dict):
                    oid = resp_l.get("orderID", "")
                    if oid or resp_l.get("success"):
                        logger.info(f"   💎 GTC ejecutada: {oid[:20]}...")
                        return True
            except Exception as e:
                logger.debug(f"   GTC falló: {str(e)[:60]}")

            return False

        except Exception as e:
            logger.error(f"   💎 Error CLOB: {e}")
            return False

    def get_stats(self) -> str:
        wr = (self.stats["wins"] / self.stats["trades"] * 100) if self.stats["trades"] > 0 else 0
        return (
            f"💎 Grinder: {self.stats['trades']} trades | "
            f"WR: {wr:.0f}% | P&L: ${self.stats['profit']:+.2f}"
        )
