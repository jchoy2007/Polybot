"""
PolyBot - Crypto Daily Strategy v2
===================================
Reescrito desde cero para ser PRÁCTICO y EFECTIVO.

El btc_15min.py original era demasiado estricto: requería momentum
en ventanas muy pequeñas, casi nunca disparaba.

Esta versión busca mercados crypto Up/Down de RESOLUCIÓN DIARIA
(más predecibles que los de 15 min) + Up/Down de HORAS (con análisis
de momentum más amplio).

Cómo funciona:
1. Escanea Gamma API buscando mercados de BTC/ETH/SOL
2. Obtiene precio REAL de Binance
3. Compara con el precio implícito de Polymarket
4. Si hay desfase (edge >= 5%) + momentum favorable → apuesta
"""

import os
import re
import json
import time
import logging
import aiohttp
from datetime import datetime, timezone
from typing import Optional, Dict, List
from config.settings import SAFETY, STATE, GAMMA_API_URL

logger = logging.getLogger("polybot.crypto")

BINANCE_API = "https://api.binance.com/api/v3"


class CryptoDailyStrategy:
    """Estrategia de crypto focused en mercados diarios y cortos."""

    CRYPTOS = {
        "BTC": {"symbol": "BTCUSDT", "keywords": ["btc", "bitcoin"]},
        "ETH": {"symbol": "ETHUSDT", "keywords": ["eth", "ethereum"]},
        "SOL": {"symbol": "SOLUSDT", "keywords": ["sol", "solana"]},
        "XRP": {"symbol": "XRPUSDT", "keywords": ["xrp", "ripple"]},
    }

    MIN_EDGE = 0.05   # 5% edge mínimo
    MIN_PRICE = 0.15  # No comprar underdogs extremos
    MAX_PRICE = 0.85  # No overpagar
    COOLDOWN_MIN = 10 # 10 min entre apuestas del mismo crypto

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_bet_time = {}
        self.traded_markets = set()
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
                    "timestamp": datetime.now().isoformat()
                })
            with open("data/bets_placed.json", "w") as f:
                json.dump(data, f, indent=2)
            self.traded_markets.add(market_id)
        except Exception:
            pass

    # =================================================================
    # PRECIOS DE BINANCE
    # =================================================================

    async def get_price(self, symbol: str) -> Optional[Dict]:
        """
        Obtiene precio actual + tendencia de 1h y 24h para una crypto.
        Retorna: {price, change_1h, change_24h, direction}
        """
        session = await self._get_session()
        try:
            # 24h stats
            async with session.get(
                f"{BINANCE_API}/ticker/24hr",
                params={"symbol": symbol}
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            price = float(data.get("lastPrice", 0))
            change_24h = float(data.get("priceChangePercent", 0))

            # Klines de 1h para momentum reciente
            async with session.get(
                f"{BINANCE_API}/klines",
                params={
                    "symbol": symbol,
                    "interval": "1h",
                    "limit": 2
                }
            ) as resp:
                change_1h = 0
                if resp.status == 200:
                    klines = await resp.json()
                    if len(klines) >= 2:
                        current = float(klines[-1][4])
                        prev = float(klines[-2][4])
                        change_1h = ((current - prev) / prev) * 100

            return {
                "price": price,
                "change_1h": change_1h,
                "change_24h": change_24h,
                "direction": "UP" if change_1h > 0 else "DOWN",
                "momentum_1h": abs(change_1h),
                "momentum_24h": abs(change_24h),
            }
        except Exception as e:
            logger.debug(f"Binance error {symbol}: {e}")
            return None

    # =================================================================
    # BUSCAR MERCADOS CRYPTO EN POLYMARKET
    # =================================================================

    async def find_crypto_markets(self) -> List[Dict]:
        """Busca todos los mercados crypto activos en Polymarket."""
        session = await self._get_session()
        all_markets = []

        try:
            for offset in [0, 100, 200, 300, 400]:
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
                    else:
                        break

            # Filtrar crypto markets
            crypto_markets = []
            for m in all_markets:
                q = (m.get("question") or "").lower()
                # Debe contener una crypto
                has_crypto = any(
                    any(kw in q for kw in info["keywords"])
                    for info in self.CRYPTOS.values()
                )
                # Debe ser un mercado direccional (up/down, above/below, dip)
                is_directional = any(kw in q for kw in [
                    "up or down", "up/down", "above", "below",
                    "dip to", "close above", "close below",
                    "between", "reach"
                ])

                if not (has_crypto and is_directional):
                    continue

                # Skip si ya apostamos
                if str(m.get("id", "")) in self.traded_markets:
                    continue

                # FILTRO CRÍTICO: solo mercados que resuelven en menos de 48h
                # (respeta SAFETY.max_resolution_days = 2). Antes podía apostar
                # en "Will XRP reach $1.40 in April?" que resuelve semanas
                # después — mucho puede pasar en ese tiempo.
                end_date_str = m.get("endDate", "")
                if end_date_str:
                    try:
                        if end_date_str.endswith("Z"):
                            end_dt = datetime.fromisoformat(
                                end_date_str.replace("Z", "+00:00"))
                        elif "+" in end_date_str[-6:]:
                            end_dt = datetime.fromisoformat(end_date_str)
                        else:
                            end_dt = datetime.fromisoformat(
                                end_date_str).replace(tzinfo=timezone.utc)
                        hours_to_resolve = (
                            end_dt - datetime.now(timezone.utc)
                        ).total_seconds() / 3600
                        # Máximo 48 horas de resolución
                        if hours_to_resolve > 48:
                            continue
                        # Mínimo 15 min (muy pronto = muy tarde para apostar)
                        if hours_to_resolve < 0.25:
                            continue
                    except Exception:
                        # Si no se puede parsear fecha, mejor saltar
                        continue

                crypto_markets.append(m)

            return crypto_markets

        except Exception as e:
            logger.error(f"Error buscando crypto markets: {e}")
            return []

    # =================================================================
    # GENERAR SEÑAL PARA UN MERCADO
    # =================================================================

    def generate_signal(self, market: Dict, price_data: Dict) -> Optional[Dict]:
        """
        Analiza un mercado crypto y genera señal de apuesta si hay edge.

        Heuristica simple:
        - "Will BTC be above $X by Y" + BTC price > $X * 1.005 + momentum UP → BET YES
        - "Will BTC be above $X by Y" + BTC price < $X * 0.995 + momentum DOWN → BET NO
        - "Will BTC dip to $X" + BTC price >> $X → BET NO
        """
        question = (market.get("question") or "").lower()
        current_price = price_data["price"]
        change_24h = price_data["change_24h"]

        # Extraer precio target de la pregunta (ej: $74,000)
        price_match = re.search(r'\$[\d,]+', market.get("question", ""))
        if not price_match:
            return None
        try:
            target_price = float(price_match.group(0).replace("$", "").replace(",", ""))
        except ValueError:
            return None

        # Obtener precios Yes/No de Polymarket
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
        except Exception:
            return None

        # Lógica: "above $X" / "close above" / "reach" → precio actual vs target
        if "above" in question or "close above" in question or "reach" in question:
            gap_pct = ((current_price - target_price) / target_price) * 100

            # Ya está claramente por encima → YES probable
            # Threshold bajado de 0.5% a 0.15% (crypto se mueve rápido)
            if gap_pct > 0.15:
                # Probabilidad escalada por distancia + momentum 24h
                our_prob = min(0.92, 0.58 + gap_pct * 0.03 + change_24h * 0.015)
                edge = our_prob - yes_price
                if (edge >= self.MIN_EDGE and
                        self.MIN_PRICE <= yes_price <= self.MAX_PRICE):
                    return {
                        "side": "YES",
                        "price": yes_price,
                        "edge": edge,
                        "prob": our_prob,
                        "reason": f"BTC ${current_price:,.0f} vs target ${target_price:,.0f} (+{gap_pct:.1f}%)"
                    }

            # Ya está claramente por debajo → NO probable
            elif gap_pct < -0.15:
                our_prob = min(0.92, 0.58 + abs(gap_pct) * 0.03 - change_24h * 0.015)
                edge = our_prob - no_price
                if (edge >= self.MIN_EDGE and
                        self.MIN_PRICE <= no_price <= self.MAX_PRICE):
                    return {
                        "side": "NO",
                        "price": no_price,
                        "edge": edge,
                        "prob": our_prob,
                        "reason": f"BTC ${current_price:,.0f} vs target ${target_price:,.0f} ({gap_pct:.1f}%)"
                    }

        # Lógica: "dip to $X" → si precio actual >> target, NO es probable
        elif "dip" in question or "below" in question:
            gap_pct = ((current_price - target_price) / target_price) * 100

            # Lejos del target = improbable que llegue → NO
            # Threshold bajado de 2% a 1% (más realista)
            if gap_pct > 1:
                our_prob = min(0.90, 0.62 + (gap_pct * 0.02))
                edge = our_prob - no_price
                if (edge >= self.MIN_EDGE and
                        self.MIN_PRICE <= no_price <= self.MAX_PRICE):
                    return {
                        "side": "NO",
                        "price": no_price,
                        "edge": edge,
                        "prob": our_prob,
                        "reason": f"BTC ${current_price:,.0f} lejos del dip target ${target_price:,.0f}"
                    }

        return None

    # =================================================================
    # CALCULAR MONTO
    # =================================================================

    def calculate_bet(self, edge: float) -> float:
        """Kelly fraction aplicado al bankroll actual."""
        bankroll = STATE.current_bankroll
        kelly = edge * SAFETY.kelly_fraction  # 0.25
        bet = bankroll * kelly
        bet = max(bet, SAFETY.min_bet_size)
        bet = min(bet, SAFETY.max_bet_absolute)
        bet = min(bet, bankroll * SAFETY.max_bet_pct)
        return round(bet, 2)

    # =================================================================
    # CICLO PRINCIPAL
    # =================================================================

    async def run_cycle(self) -> List[Dict]:
        """
        Escanea mercados crypto y retorna lista de señales para apostar.
        Main.py se encarga de ejecutar las apuestas.
        """
        if STATE.is_paused:
            return []

        logger.info("₿ Crypto Daily: Escaneando mercados...")

        # Obtener mercados crypto
        markets = await self.find_crypto_markets()
        if not markets:
            logger.info("   ₿ Sin mercados crypto activos")
            return []

        logger.info(f"   ₿ {len(markets)} mercados crypto encontrados")

        # Pre-cargar precios de las cryptos que aparezcan
        prices_cache = {}
        for crypto_key, info in self.CRYPTOS.items():
            price_data = await self.get_price(info["symbol"])
            if price_data:
                prices_cache[crypto_key] = price_data
                logger.info(
                    f"   {crypto_key}: ${price_data['price']:,.2f} | "
                    f"1h: {price_data['change_1h']:+.2f}% | "
                    f"24h: {price_data['change_24h']:+.2f}%"
                )

        # Analizar cada mercado y generar señales
        signals = []
        for m in markets:
            q = (m.get("question") or "").lower()

            # Identificar qué crypto es
            crypto = None
            for key, info in self.CRYPTOS.items():
                if any(kw in q for kw in info["keywords"]):
                    crypto = key
                    break
            if not crypto or crypto not in prices_cache:
                continue

            # Cooldown
            last = self.last_bet_time.get(crypto, 0)
            if time.time() - last < self.COOLDOWN_MIN * 60:
                continue

            signal = self.generate_signal(m, prices_cache[crypto])
            if not signal:
                continue

            bet = self.calculate_bet(signal["edge"])
            signal_info = {
                "strategy": f"CRYPTO_{crypto}",
                "crypto": crypto,
                "market_id": str(m.get("id", "")),
                "question": m.get("question", ""),
                "side": signal["side"],
                "amount": bet,
                "price": signal["price"],
                "edge": signal["edge"],
                "prob": signal["prob"],
                "reason": signal["reason"],
                "end_date": m.get("endDate", ""),
            }
            signals.append(signal_info)
            logger.warning(
                "⚠️ CRYPTO strat bajo observación (1/3 WR). "
                "Evaluación a n=4 para decidir mantener/desactivar."
            )
            logger.info(
                f"   🎯 {crypto} SEÑAL {signal['side']} | "
                f"Edge: {signal['edge']:.1%} | "
                f"Bet: ${bet:.2f} | {signal['reason']}"
            )

        return signals
