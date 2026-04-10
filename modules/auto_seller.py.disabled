"""
PolyBot - Auto Seller (Swing Trading)
=======================================
Vende posiciones automáticamente cuando:
- Suben +30% de ganancia (take profit)
- Bajan -40% de pérdida (stop loss)
- Llevan >48 horas sin resolver (liberar capital)

Top 1% de traders de Polymarket NO esperan resolución.
Compran a $0.40, venden a $0.65 cuando el precio sube.
Holding promedio: 18-72 horas.

Se ejecuta cada ciclo después de las estrategias de compra.
"""

import os
import json
import time
import logging
import aiohttp
from typing import Optional, Dict, List
from datetime import datetime, timezone
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.seller")

DATA_API_URL = "https://data-api.polymarket.com"

# Parámetros
TAKE_PROFIT_PCT = 0.30    # Vender si sube +30%
STOP_LOSS_PCT = -0.40     # Vender si baja -40%
MAX_HOLD_HOURS = 48       # Vender si lleva >48 horas
MIN_POSITION_VALUE = 1.0  # No vender posiciones de <$1 (fees comen la ganancia)
COOLDOWN_MINUTES = 30     # No revender dentro de 30 min


class AutoSeller:
    """Vende posiciones automáticamente para swing trading."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_run = 0
        self.min_interval = 300  # Revisar cada 5 min
        self.sold_markets: set = set()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def run_cycle(self) -> List[Dict]:
        """
        v2: Calcula P&L usando precio de compra real (de bets_placed.json)
        en vez de percentPnl de la Data API (que es corrupto).
        Solo vende si el precio bajó más de 40% desde la compra.
        NO toca posiciones >85¢ (Harvests).
        """
        if STATE.is_paused:
            return []

        now = time.time()
        if now - self.last_run < self.min_interval:
            return []
        self.last_run = now

        if SAFETY.dry_run:
            return []

        positions = await self._get_positions()
        if not positions:
            return []

        # Cargar precios de compra desde bets_placed.json
        buy_prices = self._load_buy_prices()

        sales = []
        for pos in positions:
            action = self._should_sell_v2(pos, buy_prices)
            if action:
                logger.info(
                    f"   💰 {action['reason']}: {action['title'][:40]} | "
                    f"Compra: ${action['buy_price']:.2f} → Ahora: ${action['cur_price']:.2f}"
                )

                if not SAFETY.dry_run:
                    sold = await self._execute_sell(pos, action)
                    if sold:
                        sales.append(sold)

        return sales

    def _load_buy_prices(self) -> Dict[str, float]:
        """Carga precios de compra desde bets_placed.json y trade_results.json"""
        prices = {}
        # Desde trade_results.json (tiene el precio de compra)
        try:
            with open("data/trade_results.json", "r") as f:
                trades = json.load(f)
                for t in trades:
                    mid = t.get("market_id", "")
                    if mid:
                        prices[mid] = float(t.get("price", 0))
        except:
            pass
        return prices

    async def _get_positions(self) -> List[Dict]:
        """Obtiene posiciones actuales de la Data API."""
        session = await self._get_session()
        pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        if not pk:
            return []

        try:
            from web3 import Web3
            address = Web3().eth.account.from_key(pk).address
        except:
            return []

        positions = []
        for addr in [funder, address]:
            if not addr:
                continue
            try:
                async with session.get(
                    f"{DATA_API_URL}/positions?user={addr.lower()}"
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list) and len(data) > 0:
                            positions = data
                            break
            except:
                continue

        return positions

    def _should_sell_v2(self, pos: Dict, buy_prices: Dict[str, float]) -> Optional[Dict]:
        """
        v2: Decide si vender usando precio de compra REAL, no Data API.
        Solo vende si el precio actual cayó más de 40% desde la compra.
        NUNCA vende posiciones >85¢ (Harvests/casi ganadas).
        """
        title = (pos.get("title") or pos.get("question") or "?")[:50]
        market_id = pos.get("market_id") or pos.get("conditionId") or ""
        asset = pos.get("asset") or ""
        side = pos.get("outcome") or "?"
        cur_price = float(pos.get("curPrice") or 0)
        value = float(pos.get("currentValue") or 0)

        # Ignorar posiciones muy chicas
        if value < MIN_POSITION_VALUE:
            return None

        # Ya vendimos esta?
        if market_id in self.sold_markets:
            return None

        # NUNCA vender posiciones >85¢ — son casi ganadoras, esperar resolución
        if cur_price >= 0.85:
            return None

        # NUNCA vender posiciones muy baratas — ya perdidas, no vale la pena
        if cur_price <= 0.05:
            return None

        # Buscar precio de compra en nuestros registros
        buy_price = buy_prices.get(market_id, 0)
        if buy_price <= 0:
            return None  # No tenemos datos de compra, no vender

        # Calcular P&L real
        real_pnl_pct = (cur_price - buy_price) / buy_price

        action = {
            "market_id": market_id,
            "asset": asset,
            "title": title,
            "side": side,
            "value": value,
            "cur_price": cur_price,
            "buy_price": buy_price,
            "pnl_pct": real_pnl_pct,
            "reason": "",
            "token_id": asset,
        }

        # STOP LOSS: Precio cayó más de 40% desde compra
        if real_pnl_pct <= STOP_LOSS_PCT:
            action["reason"] = f"STOP LOSS ({real_pnl_pct:+.0%}) compra@{buy_price:.2f}"
            return action

        # TAKE PROFIT: Precio subió más de 30% desde compra
        if real_pnl_pct >= TAKE_PROFIT_PCT:
            action["reason"] = f"TAKE PROFIT ({real_pnl_pct:+.0%}) compra@{buy_price:.2f}"
            return action

        return None

    async def _execute_sell(self, pos: Dict, action: Dict) -> Optional[Dict]:
        """Ejecuta la venta de una posición."""
        token_id = action.get("token_id", "")
        if not token_id:
            return None

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL

            pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if not pk:
                return None
            pk_clean = pk[2:] if pk.startswith("0x") else pk

            client = ClobClient(
                host="https://clob.polymarket.com",
                key=pk_clean, chain_id=137, signature_type=0
            )
            client.set_api_creds(client.create_or_derive_api_creds())

            # Vender con Market Order (FOK)
            size = float(pos.get("size") or 0)
            if size <= 0:
                return None

            try:
                mo = MarketOrderArgs(
                    token_id=token_id,
                    amount=round(size, 2),
                    side=SELL
                )
                signed = client.create_market_order(mo)
                resp = client.post_order(signed, OrderType.FOK)

                if resp and isinstance(resp, dict):
                    oid = resp.get("orderID", "")
                    if resp.get("success") or oid:
                        self.sold_markets.add(action["market_id"])
                        proceeds = action["value"]
                        STATE.current_bankroll += proceeds
                        logger.info(
                            f"   ✅ VENDIDO: {action['title'][:35]} | "
                            f"+${proceeds:.2f} | {action['reason']}"
                        )
                        return {
                            "market_id": action["market_id"],
                            "question": action["title"],
                            "side": action["side"],
                            "proceeds": proceeds,
                            "pnl": action["pnl"],
                            "reason": action["reason"],
                        }
            except Exception as e:
                logger.debug(f"   Venta falló: {str(e)[:60]}")

            return None

        except Exception as e:
            logger.error(f"   Error vendiendo: {e}")
            return None

    def get_stats(self) -> str:
        return f"💰 AutoSeller: {len(self.sold_markets)} posiciones vendidas"
