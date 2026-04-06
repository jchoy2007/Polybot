"""
PolyBot - Strategy 6: Flash Crash Detector
============================================
Detecta caídas súbitas de probabilidad en mercados crypto de Polymarket.
Cuando un mercado cae >8% entre ciclos sin que el activo subyacente
se mueva, es un crash de liquidez → compra el dip.

Funciona DENTRO del ciclo de 15 min:
- Cada ciclo guarda precios actuales en data/flash_prices.json
- Siguiente ciclo compara con precios anteriores
- Si detecta crash de liquidez → ejecuta trade
"""

import os
import json
import time
import logging
import aiohttp
from typing import Optional, Dict, List
from datetime import datetime, timezone
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.flash")

GAMMA_API_URL = "https://gamma-api.polymarket.com"
BINANCE_API = "https://api.binance.com/api/v3"
PRICES_FILE = "data/flash_prices.json"

# Parámetros
CRASH_THRESHOLD = -0.08    # -8% en probabilidad del mercado
CRYPTO_MOVE_MAX = 0.02     # Si crypto se movió <2%, es crash de liquidez
MIN_EDGE_AFTER_CRASH = 0.10
MAX_BET_FLASH = 5.0        # Conservador (especulativo)
COOLDOWN_SECONDS = 900     # 15 min cooldown por mercado


class FlashCrashDetector:
    """Detecta flash crashes en mercados crypto de Polymarket."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.price_history = self._load_prices()
        self.traded_markets = set()
        self._load_traded()
        self.cryptos = {
            "BTC": "BTCUSDT",
            "ETH": "ETHUSDT",
            "SOL": "SOLUSDT",
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

    def _load_prices(self) -> Dict:
        """Carga historial de precios entre ciclos."""
        try:
            with open(PRICES_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"markets": {}, "crypto": {}, "last_update": 0}

    def _save_prices(self):
        """Guarda precios para el próximo ciclo."""
        os.makedirs("data", exist_ok=True)
        try:
            with open(PRICES_FILE, "w") as f:
                json.dump(self.price_history, f, indent=2)
        except Exception as e:
            logger.debug(f"Error guardando flash prices: {e}")

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
                    "strategy": "FLASH_CRASH"
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
        Ciclo del flash crash detector:
        1. Busca mercados crypto activos
        2. Obtiene precios actuales de crypto (Binance)
        3. Compara precios de mercado vs ciclo anterior
        4. Si hay crash de liquidez → compra
        5. Guarda precios para próximo ciclo
        """
        if STATE.is_paused:
            return None

        logger.info("⚡ Flash Crash: Escaneando mercados crypto...")

        # 1. Obtener precios crypto actuales
        crypto_prices = await self._get_crypto_prices()
        if not crypto_prices:
            logger.info("   ⚡ No se pudieron obtener precios crypto")
            return None

        # Guardar precios crypto
        for asset, price in crypto_prices.items():
            prev = self.price_history.get("crypto", {}).get(asset, {})
            self.price_history.setdefault("crypto", {})[asset] = {
                "price": price,
                "prev_price": prev.get("price", price),
                "timestamp": time.time(),
            }

        # 2. Buscar mercados crypto en Polymarket
        crypto_markets = await self._find_crypto_markets()
        if not crypto_markets:
            logger.info("   ⚡ No se encontraron mercados crypto de corto plazo")
            self._save_prices()
            return None

        logger.info(f"   ⚡ {len(crypto_markets)} mercados crypto encontrados")

        # 3. Comparar con precios anteriores y buscar crashes
        trade = None
        for market in crypto_markets:
            market_id = str(market.get("id", ""))
            question = market.get("question", "")

            # Ya tradeamos este?
            if market_id in self.traded_markets:
                continue

            # Obtener precio YES actual
            outcomes = market.get("outcomePrices", "[]")
            if isinstance(outcomes, str):
                prices = json.loads(outcomes)
            else:
                prices = outcomes
            if len(prices) < 2:
                continue

            yes_price = float(prices[0])

            # Obtener precio anterior
            prev_data = self.price_history.get("markets", {}).get(market_id, {})
            prev_yes = prev_data.get("yes_price")
            prev_time = prev_data.get("timestamp", 0)

            # Guardar precio actual
            self.price_history.setdefault("markets", {})[market_id] = {
                "yes_price": yes_price,
                "question": question[:60],
                "timestamp": time.time(),
            }

            # Necesitamos datos previos para comparar
            if prev_yes is None:
                continue

            # Muy viejo? (>1 hora = no es flash crash)
            if time.time() - prev_time > 3600:
                continue

            # Cooldown
            last_trade_time = prev_data.get("last_trade", 0)
            if time.time() - last_trade_time < COOLDOWN_SECONDS:
                continue

            # 4. Calcular cambio
            change = (yes_price - prev_yes) / prev_yes if prev_yes > 0 else 0

            if change > CRASH_THRESHOLD:
                continue  # No hay crash

            logger.info(f"   ⚡ CRASH: {question[:45]} | {change:+.1%} ({prev_yes:.2f} → {yes_price:.2f})")

            # 5. Verificar que el activo subyacente no se movió mucho
            asset = self._detect_asset(question)
            if asset and asset in crypto_prices:
                asset_prev = self.price_history.get("crypto", {}).get(asset, {}).get("prev_price", 0)
                asset_now = crypto_prices[asset]
                if asset_prev > 0:
                    asset_change = (asset_now - asset_prev) / asset_prev
                    if abs(asset_change) > CRYPTO_MOVE_MAX:
                        logger.info(f"      {asset} se movió {asset_change:+.1%} → crash fundamental, skip")
                        continue
                    logger.info(f"      {asset} solo se movió {asset_change:+.1%} → crash de LIQUIDEZ!")

            # 6. Calcular edge
            # Estimamos que el precio real está entre el anterior y el actual
            fair_value = (prev_yes + yes_price) / 2
            edge = fair_value - yes_price

            if edge < MIN_EDGE_AFTER_CRASH:
                logger.info(f"      Edge {edge:.1%} insuficiente")
                continue

            # 7. Ejecutar trade
            tokens = market.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if len(tokens) < 2:
                continue

            token_id = tokens[0]  # Compramos YES (el que crasheó)
            bet_amount = min(MAX_BET_FLASH, STATE.current_bankroll * 0.05)
            bet_amount = max(2.0, round(bet_amount, 2))

            trade = {
                "strategy": "FLASH_CRASH",
                "timestamp": datetime.now().isoformat(),
                "market_id": market_id,
                "question": question,
                "side": "YES",
                "amount": bet_amount,
                "price": yes_price,
                "edge": edge,
                "crash_pct": change,
                "prev_price": prev_yes,
                "asset": asset,
                "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE"
            }

            if SAFETY.dry_run:
                trade["status"] = "SIMULATED"
                logger.info(f"      🏃 [DRY RUN] YES ${bet_amount:.2f} @ {yes_price:.2f} | crash={change:+.1%}")
            else:
                logger.info(f"      💰 [LIVE] YES ${bet_amount:.2f} @ {yes_price:.2f}")
                try:
                    executed = await self._execute_real_order(token_id, yes_price, bet_amount)
                    if executed:
                        trade["status"] = "EXECUTED"
                        STATE.current_bankroll -= bet_amount
                        self.traded_markets.add(market_id)
                        self._save_bet(market_id, question)
                        STATE.total_trades += 1
                        STATE.open_positions += 1
                        # Marcar cooldown
                        self.price_history["markets"][market_id]["last_trade"] = time.time()
                        logger.info(f"      ✅ Flash crash trade ejecutado!")
                    else:
                        trade["status"] = "FAILED"
                except Exception as e:
                    trade["status"] = "ERROR"
                    trade["error"] = str(e)

            break  # Un trade por ciclo

        # Limpiar mercados viejos del historial (>24h)
        now = time.time()
        old_ids = [mid for mid, data in self.price_history.get("markets", {}).items()
                   if now - data.get("timestamp", 0) > 86400]
        for mid in old_ids:
            del self.price_history["markets"][mid]

        self.price_history["last_update"] = now
        self._save_prices()

        if not trade:
            logger.info("   ⚡ Sin flash crashes detectados")

        return trade

    # ═══════════════════════════════════════════════════════════════
    # BUSCAR MERCADOS CRYPTO
    # ═══════════════════════════════════════════════════════════════

    async def _find_crypto_markets(self) -> List[Dict]:
        """Busca mercados crypto de corto plazo."""
        session = await self._get_session()
        crypto_kw = ["btc", "bitcoin", "eth", "ethereum", "sol", "solana"]
        time_kw = ["15", "minute", "hour", "1-hour", "5-min", "30-min", "up or down"]

        markets = []
        for offset in [0, 100, 200, 300, 400, 500]:
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
                            if has_crypto and has_time:
                                # Solo mercados que resuelven en <24h
                                end_str = m.get("endDate", "")
                                if end_str:
                                    try:
                                        end_dt = datetime.fromisoformat(
                                            end_str.replace("Z", "+00:00"))
                                        hours = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                                        if 0 < hours < 24:
                                            markets.append(m)
                                    except:
                                        pass
            except Exception:
                break

        return markets

    # ═══════════════════════════════════════════════════════════════
    # PRECIOS CRYPTO (Binance)
    # ═══════════════════════════════════════════════════════════════

    async def _get_crypto_prices(self) -> Dict[str, float]:
        """Obtiene precios spot de BTC, ETH, SOL desde Binance."""
        session = await self._get_session()
        prices = {}

        for asset, symbol in self.cryptos.items():
            try:
                async with session.get(
                    f"{BINANCE_API}/ticker/price",
                    params={"symbol": symbol}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        prices[asset] = float(data["price"])
            except Exception as e:
                logger.debug(f"   ⚡ Binance {asset}: {e}")

        return prices

    def _detect_asset(self, question: str) -> Optional[str]:
        q = question.lower()
        if "btc" in q or "bitcoin" in q:
            return "BTC"
        elif "eth" in q or "ethereum" in q:
            return "ETH"
        elif "sol" in q or "solana" in q:
            return "SOL"
        return None

    # ═══════════════════════════════════════════════════════════════
    # EJECUCIÓN REAL
    # ═══════════════════════════════════════════════════════════════

    async def _execute_real_order(self, token_id: str, price: float,
                                   amount: float) -> bool:
        """Ejecuta orden real (mismo patrón que btc_15min)."""
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

            # FOK
            try:
                mo = MarketOrderArgs(token_id=token_id, amount=amount, side=BUY)
                signed = client.create_market_order(mo)
                resp = client.post_order(signed, OrderType.FOK)
                if resp and isinstance(resp, dict):
                    oid = resp.get("orderID", "")
                    if (resp.get("success") or resp.get("status") == "matched") and oid:
                        logger.info(f"      ✅ FOK ejecutada: {oid[:20]}...")
                        return True
            except Exception as e:
                logger.debug(f"      FOK falló: {str(e)[:60]}")

            # GTC
            try:
                limit_price = min(price + 0.03, 0.95)
                size = round(amount / max(price, 0.01), 2)
                lo = OrderArgs(token_id=token_id, price=round(limit_price, 2),
                               size=size, side=BUY)
                signed_l = client.create_order(lo)
                resp_l = client.post_order(signed_l, OrderType.GTC)
                if resp_l and isinstance(resp_l, dict):
                    oid = resp_l.get("orderID", "")
                    if oid or resp_l.get("success"):
                        logger.info(f"      ✅ GTC ejecutada: {oid[:20]}...")
                        return True
            except Exception as e:
                logger.debug(f"      GTC falló: {str(e)[:60]}")

            return False
        except Exception as e:
            logger.error(f"      Error CLOB: {e}")
            return False

    def get_stats(self) -> str:
        tracked = len(self.price_history.get("markets", {}))
        crypto = list(self.price_history.get("crypto", {}).keys())
        return f"⚡ Flash: {tracked} mercados tracked, crypto: {', '.join(crypto) or 'ninguno'}"
