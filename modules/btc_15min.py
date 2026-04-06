"""
PolyBot - Estrategia BTC 15-Min Up/Down
=========================================
LA MINA DE ORO: Cada 15 min Polymarket abre un mercado
"¿BTC sube o baja?". Este módulo compara el precio real
de Binance con el precio en Polymarket y apuesta cuando
detecta un desfase (lag) a nuestro favor.

Un bot similar convirtió $313 en $414K en un mes.
"""

import logging
import json
import asyncio
import aiohttp
import time
from typing import Optional, Dict, Tuple
from datetime import datetime
from config.settings import SAFETY, STATE, GAMMA_API_URL

logger = logging.getLogger("polybot.btc15m")

BINANCE_API = "https://api.binance.com/api/v3"
COINGECKO_API = "https://api.coingecko.com/api/v3"


class BTC15MinStrategy:
    """
    Estrategia BTC 15-minutos Up/Down.
    
    Cómo funciona:
    1. Obtiene el precio actual de BTC en Binance (precio real)
    2. Busca el mercado activo "BTC 15-min up/down" en Polymarket
    3. Calcula el momentum (¿BTC está subiendo o bajando?)
    4. Si hay señal clara + precio favorable en Polymarket → apuesta
    5. Repite cada minuto buscando la mejor entrada
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.price_history = []
        self.last_bet_time = {}  # Por crypto: {"BTC": timestamp, "ETH": ...}
        self.min_bet_interval = 300  # 5 min entre apuestas por crypto
        self.wins = 0
        self.losses = 0
        self.total_profit = 0.0
        # Cryptos a monitorear
        self.cryptos = {
            "BTC": {"symbol": "BTCUSDT", "name": "bitcoin", "keywords": ["btc", "bitcoin"]},
            "ETH": {"symbol": "ETHUSDT", "name": "ethereum", "keywords": ["eth", "ethereum"]},
            "SOL": {"symbol": "SOLUSDT", "name": "solana", "keywords": ["sol", "solana"]},
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _save_bet(self, market_id: str, question: str = ""):
        """Guarda apuesta en disco para persistencia entre reinicios."""
        try:
            import os
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
        except Exception:
            pass

    # =================================================================
    # OBTENER PRECIO DE BTC EN TIEMPO REAL
    # =================================================================

    async def get_btc_price_binance(self) -> Optional[float]:
        """Obtiene el precio actual de BTC/USDT de Binance."""
        return await self.get_crypto_price("BTCUSDT")

    async def get_crypto_price(self, symbol: str = "BTCUSDT") -> Optional[float]:
        """Obtiene el precio actual de cualquier crypto en Binance."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{BINANCE_API}/ticker/price",
                params={"symbol": symbol}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["price"])
        except Exception as e:
            logger.debug(f"Error Binance: {e}")

        # Fallback: CoinGecko
        try:
            async with session.get(
                f"{COINGECKO_API}/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["bitcoin"]["usd"])
        except Exception as e:
            logger.error(f"Error obteniendo precio BTC: {e}")
            return None

    async def get_btc_price_change(self, window_seconds: int = 60) -> Optional[Dict]:
        """Obtiene cambio de precio de BTC."""
        return await self.get_crypto_price_change("BTCUSDT")

    async def get_crypto_price_change(self, symbol: str = "BTCUSDT") -> Optional[Dict]:
        """Obtiene el cambio de precio de cualquier crypto."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{BINANCE_API}/klines",
                params={
                    "symbol": symbol,
                    "interval": "1m",
                    "limit": 5
                }
            ) as resp:
                if resp.status == 200:
                    klines = await resp.json()
                    if len(klines) >= 2:
                        current_close = float(klines[-1][4])
                        prev_close = float(klines[-2][4])
                        five_min_ago = float(klines[0][4])
                        change_1m = ((current_close - prev_close) / prev_close) * 100
                        change_5m = ((current_close - five_min_ago) / five_min_ago) * 100
                        return {
                            "price": current_close,
                            "change_1m": change_1m,
                            "change_5m": change_5m,
                            "direction": "UP" if change_1m > 0 else "DOWN",
                            "momentum": abs(change_1m)
                        }
        except Exception as e:
            logger.error(f"Error obteniendo klines: {e}")
        return None

    # =================================================================
    # BUSCAR MERCADO BTC 15-MIN EN POLYMARKET
    # =================================================================

    async def find_btc_15min_market(self) -> Optional[Dict]:
        """Busca mercado BTC up/down (legacy)."""
        return await self.find_crypto_market("BTC")

    async def find_crypto_market(self, crypto: str = "BTC") -> Optional[Dict]:
        """
        Busca mercado up/down activo de HOY para cualquier crypto.
        Busca en 500 mercados con diagnóstico detallado.
        """
        from datetime import timezone
        session = await self._get_session()
        keywords = self.cryptos.get(crypto, {}).get("keywords", [crypto.lower()])

        # Fecha de hoy en múltiples formatos para matching
        now_utc = datetime.now(timezone.utc)
        day = now_utc.day
        month_full = now_utc.strftime("%B").lower()   # "april"
        month_short = now_utc.strftime("%b").lower()   # "apr"
        today_strs = [
            f"{month_full} {day}",      # "april 2"
            f"{month_short} {day}",     # "apr 2"
            f"{month_full} {day},",     # "april 2," (con coma)
            f"{month_short} {day},",    # "apr 2,"
        ]
        today_date = now_utc.strftime("%Y-%m-%d")  # "2026-04-02"

        try:
            all_markets = []
            for offset in [0, 100, 200, 300, 400, 500, 600]:
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
                            break  # No más resultados
                        all_markets.extend(batch)
                    else:
                        break

            # === DIAGNÓSTICO: Encontrar TODOS los mercados crypto up/down ===
            crypto_updown_all = []
            crypto_updown_today = []
            best_candidate = None

            for m in all_markets:
                question = m.get("question", "").lower()
                has_keyword = any(kw in question for kw in keywords)
                is_updown = ("up or down" in question or "up/down" in question)

                if not (has_keyword and is_updown):
                    continue

                crypto_updown_all.append(m)

                # Método 1: Buscar fecha de hoy en texto del question
                text_match = any(ts in question for ts in today_strs)

                # Método 2: Buscar por endDate del mercado
                end_date_str = m.get("endDate") or m.get("end_date_iso") or ""
                date_match = today_date in end_date_str

                if not (text_match or date_match):
                    continue

                crypto_updown_today.append(m)

                # Timing check
                if not end_date_str:
                    continue

                try:
                    if end_date_str.endswith("Z"):
                        end_dt = datetime.fromisoformat(
                            end_date_str.replace("Z", "+00:00"))
                    elif "+" in end_date_str[-6:]:
                        end_dt = datetime.fromisoformat(end_date_str)
                    else:
                        end_dt = datetime.fromisoformat(
                            end_date_str).replace(tzinfo=timezone.utc)

                    minutes_remaining = (end_dt - now_utc).total_seconds() / 60.0

                    # Solo apostar cuando falten < 10 min (más predecible)
                    if minutes_remaining < 0.1:
                        continue  # Ya expirado
                    if minutes_remaining > 10:
                        continue  # Muy lejano, dirección puede cambiar

                    # Preferir el que tiene MENOS tiempo (más cerca de cerrar)
                    if best_candidate is None or minutes_remaining < best_candidate[1]:
                        best_candidate = (m, minutes_remaining)

                except Exception:
                    continue

            # === LOG DIAGNÓSTICO ===
            if crypto == "BTC":  # Solo log detallado para BTC (evitar spam)
                logger.info(
                    f"   🔍 Crypto scan: {len(all_markets)} total | "
                    f"{len(crypto_updown_all)} {crypto} up/down | "
                    f"{len(crypto_updown_today)} de hoy"
                )
                # Mostrar sample de las fechas que SÍ hay
                if crypto_updown_all and not crypto_updown_today:
                    samples = crypto_updown_all[:3]
                    dates = [s.get("question", "")[:60] for s in samples]
                    logger.info(f"   📅 Fechas encontradas: {dates}")

            if best_candidate:
                m, mins = best_candidate
                logger.info(
                    f"   ✅ {crypto} mercado de hoy encontrado: "
                    f"{m.get('question', '')[:50]} | "
                    f"{mins:.1f} min restantes"
                )
                return m

            return None

        except Exception as e:
            logger.error(f"Error buscando mercado {crypto}: {e}")
            return None

    # =================================================================
    # SEÑAL DE TRADING
    # =================================================================

    def generate_signal(self, price_data: Dict, market: Dict) -> Optional[Dict]:
        """
        Genera señal de trading basada en momentum de BTC
        vs precios de Polymarket.
        
        Lógica:
        - Si BTC sube con momentum fuerte Y el precio de "Up" en 
          Polymarket está barato → comprar UP
        - Si BTC baja con momentum fuerte Y el precio de "Down"
          está barato → comprar DOWN
        """
        import json

        direction = price_data["direction"]
        momentum = price_data["momentum"]
        change_5m = price_data["change_5m"]

        # Necesitamos momentum FUERTE (no micro-movimientos)
        if momentum < 0.015:  # 0.015% en 1 min = movimiento real
            return None

        # Necesitamos cambio de 5 min significativo
        if abs(change_5m) < 0.05:  # 0.05% en 5 min mínimo
            return None

        # Obtener precios del mercado
        try:
            outcomes = market.get("outcomePrices", "[]")
            if isinstance(outcomes, str):
                prices = json.loads(outcomes)
            else:
                prices = outcomes

            if len(prices) < 2:
                return None

            up_price = float(prices[0])
            down_price = float(prices[1])
        except:
            return None

        # Generar señal
        signal = None

        # FILTRO CRÍTICO: No apostar en mercados ya decididos
        MIN_PRICE = 0.20  # No apostar en lados casi muertos
        MAX_PRICE = 0.65  # No overpagar

        if direction == "UP" and change_5m > 0.05:
            if MIN_PRICE <= up_price <= MAX_PRICE:
                confidence = min(0.85, 0.52 + (momentum * 8))
                edge = confidence - up_price
                if edge > 0.10 and confidence >= 0.65:
                    signal = {
                        "side": "UP",
                        "price": up_price,
                        "confidence": confidence,
                        "edge": edge,
                        "reason": f"BTC subiendo {change_5m:+.2f}% en 5m, Up a ${up_price:.3f}"
                    }

        elif direction == "DOWN" and change_5m < -0.05:
            if MIN_PRICE <= down_price <= MAX_PRICE:
                confidence = min(0.85, 0.52 + (momentum * 8))
                edge = confidence - down_price
                if edge > 0.10 and confidence >= 0.65:
                    signal = {
                        "side": "DOWN",
                        "price": down_price,
                        "confidence": confidence,
                        "edge": edge,
                        "reason": f"BTC bajando {change_5m:+.2f}% en 5m, Down a ${down_price:.3f}"
                    }

        return signal

    # =================================================================
    # CALCULAR MONTO DE APUESTA
    # =================================================================

    def calculate_bet_amount(self, edge: float, confidence: float) -> float:
        """Calcula el monto de apuesta con Quarter Kelly + auto-scaling."""
        bankroll = STATE.current_bankroll

        # Quarter Kelly
        kelly = edge * SAFETY.kelly_fraction  # 0.25

        bet = bankroll * kelly
        bet = max(bet, 1.0)   # Mínimo $1

        # Usar el max_bet_absolute que se auto-escala en main.py
        bet = min(bet, SAFETY.max_bet_absolute)
        bet = min(bet, bankroll * SAFETY.max_bet_pct)  # Nunca > 8% del bankroll

        return round(bet, 2)

    # =================================================================
    # EJECUTAR CICLO BTC 15-MIN
    # =================================================================

    async def run_cycle(self) -> Optional[Dict]:
        """
        Ejecuta un ciclo escaneando BTC, ETH y SOL.
        Apuesta en el primero que tenga señal clara.
        """
        if STATE.is_paused:
            return None

        logger.info("₿ Crypto 15-min: Escaneando BTC, ETH, SOL...")

        for crypto_name, crypto_info in self.cryptos.items():
            # Verificar cooldown por crypto
            last_time = self.last_bet_time.get(crypto_name, 0)
            if time.time() - last_time < self.min_bet_interval:
                continue

            # Paso 1: Obtener precio y momentum
            price_data = await self.get_crypto_price_change(crypto_info["symbol"])
            if not price_data:
                continue

            logger.info(
                f"   {crypto_name}: ${price_data['price']:,.0f} | "
                f"1m: {price_data['change_1m']:+.3f}% | "
                f"5m: {price_data['change_5m']:+.3f}% | "
                f"Dir: {price_data['direction']}"
            )

            # Paso 2: Buscar mercado activo
            market = await self.find_crypto_market(crypto_name)
            if not market:
                continue

            logger.info(f"   Mercado: {market.get('question', '')[:60]}")

            # Verificar timing del mercado
            end_date_str = market.get("endDate") or market.get("end_date_iso")
            mins_left = "?"
            if end_date_str:
                try:
                    from datetime import timezone as tz
                    if end_date_str.endswith("Z"):
                        end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                    else:
                        end_dt = datetime.fromisoformat(end_date_str)
                    mins_left = f"{(end_dt - datetime.now(tz.utc)).total_seconds() / 60:.1f}"
                except:
                    pass
            logger.info(f"   ⏰ Minutos restantes: {mins_left}")

            # Paso 3: Generar señal
            signal = self.generate_signal(price_data, market)
            if not signal:
                logger.info(f"   {crypto_name}: Sin señal clara")
                continue

            # Paso 4: Calcular apuesta
            bet_amount = self.calculate_bet_amount(signal["edge"], signal["confidence"])

            logger.info(
                f"   🎯 {crypto_name} SEÑAL {signal['side']}! "
                f"Edge: {signal['edge']:.1%} | "
                f"Conf: {signal['confidence']:.1%} | "
                f"Apuesta: ${bet_amount:.2f}"
            )

            # Paso 5: Ejecutar
            trade = {
                "strategy": f"CRYPTO_15MIN_{crypto_name}",
                "timestamp": datetime.now().isoformat(),
                "crypto": crypto_name,
                "market": market.get("question", ""),
                "market_id": market.get("id", ""),
                "side": signal["side"],
                "amount": bet_amount,
                "price": signal["price"],
                "edge": signal["edge"],
                "confidence": signal["confidence"],
                "crypto_price": price_data["price"],
                "change_5m": price_data["change_5m"],
                "reason": signal["reason"],
                "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE"
            }

            if SAFETY.dry_run:
                logger.info(f"   🏃 [DRY RUN] {crypto_name} simulado: ${bet_amount:.2f} {signal['side']}")
            else:
                logger.info(f"   💰 [LIVE] {crypto_name} real: ${bet_amount:.2f} {signal['side']}")
                try:
                    executed = await self._execute_real_order(market, signal, bet_amount)
                    if executed:
                        trade["status"] = "EXECUTED"
                        STATE.current_bankroll -= bet_amount
                        self._save_bet(market.get("id", ""), market.get("question", ""))
                        logger.info(f"   ✅ {crypto_name} ejecutada! Capital: ${STATE.current_bankroll:.2f}")
                    else:
                        trade["status"] = "FAILED"
                except Exception as e:
                    trade["status"] = "ERROR"
                    logger.error(f"   Error {crypto_name}: {e}")

            self.last_bet_time[crypto_name] = time.time()
            STATE.total_trades += 1
            STATE.open_positions += 1
            return trade

        logger.info("   Crypto 15-min: Sin señales en BTC/ETH/SOL")
        return None

    async def _execute_real_order(self, market: Dict, signal: Dict,
                                   amount: float) -> bool:
        """Ejecuta una orden real en el CLOB con FOK → GTC fallback."""
        import json as json_mod
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY
            import os

            pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if not pk:
                return False
            pk_clean = pk[2:] if pk.startswith("0x") else pk

            client = ClobClient(
                host="https://clob.polymarket.com",
                key=pk_clean, chain_id=137, signature_type=0
            )
            client.set_api_creds(client.create_or_derive_api_creds())

            # Obtener token_id
            tokens_str = market.get("clobTokenIds", "[]")
            if isinstance(tokens_str, str):
                tokens = json_mod.loads(tokens_str)
            else:
                tokens = tokens_str

            if len(tokens) < 2:
                return False

            token_id = tokens[0] if signal["side"] == "UP" else tokens[1]

            # === INTENTO 1: FOK (Fill-Or-Kill) ===
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
                        logger.info(f"   ✅ FOK ejecutada: {oid[:20]}...")
                        return True
            except Exception as e:
                logger.debug(f"   FOK falló: {str(e)[:60]}")

            # === INTENTO 2: GTC (limit order) ===
            try:
                price = signal.get("price", 0.50)
                limit_price = min(price + 0.03, 0.95)  # Pagar un poco más para llenar
                size = amount / max(price, 0.01)

                lo = OrderArgs(
                    token_id=token_id,
                    price=round(limit_price, 2),
                    size=round(size, 2),
                    side=BUY
                )
                signed_l = client.create_order(lo)
                resp_l = client.post_order(signed_l, OrderType.GTC)

                if resp_l and isinstance(resp_l, dict):
                    oid = resp_l.get("orderID", "")
                    if oid or resp_l.get("success"):
                        logger.info(f"   ✅ GTC ejecutada: {oid[:20]}...")
                        return True
            except Exception as e:
                logger.debug(f"   GTC falló: {str(e)[:60]}")

            logger.warning(f"   ❌ Orden no ejecutada (FOK y GTC fallaron)")
            return False

        except Exception as e:
            logger.error(f"   Error CLOB: {e}")
            return False

    def get_stats(self) -> str:
        """Resumen de la estrategia Crypto 15-min."""
        total = self.wins + self.losses
        wr = (self.wins / total * 100) if total > 0 else 0
        return (
            f"₿ Crypto 15-min (BTC/ETH/SOL): {total} trades | "
            f"Win: {self.wins} | Loss: {self.losses} | "
            f"WR: {wr:.0f}% | P&L: ${self.total_profit:+.2f}"
        )
