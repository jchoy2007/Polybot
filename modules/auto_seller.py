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
        DESACTIVADO TEMPORALMENTE — el percentPnl de la Data API
        de Polymarket reporta valores corruptos que triggereaban ventas
        incorrectas. Se perdieron ~$10+ vendiendo Harvests buenos.
        Se reactivará cuando tengamos un método confiable de calcular P&L.
        """
        return []

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

    def _should_sell(self, pos: Dict) -> Optional[Dict]:
        """Decide si vender una posición."""
        title = (pos.get("title") or pos.get("question") or "?")[:50]
        market_id = pos.get("market_id") or pos.get("conditionId") or ""
        asset = pos.get("asset") or ""
        side = pos.get("outcome") or "?"
        size = float(pos.get("size") or 0)
        cur_price = float(pos.get("curPrice") or 0)
        value = float(pos.get("currentValue") or 0)
        pnl = float(pos.get("cashPnl") or 0)
        pnl_pct = float(pos.get("percentPnl") or 0)

        # Ignorar posiciones muy chicas
        if value < MIN_POSITION_VALUE:
            return None

        # Ignorar si ya vendimos o está casi resuelto (>95¢)
        if market_id in self.sold_markets:
            return None

        # No vender posiciones que están a punto de ganar (>92¢)
        if cur_price >= 0.92:
            return None

        # No vender posiciones muy baratas (probablemente ya perdidas, mejor esperar)
        if cur_price <= 0.05:
            return None

        # NO vender posiciones Harvest (>90¢ al comprar) — esas esperan resolución
        # El percentPnl de la Data API es unreliable, usar precio actual vs 0.90
        if cur_price >= 0.85:
            return None  # Probablemente un harvest, dejarlo resolver

        # Ignorar posiciones con P&L porcentajes locos (bug de Data API)
        if abs(pnl_pct) > 5.0:  # Más de 500% es imposible, dato corrupto
            return None

        action = {
            "market_id": market_id,
            "asset": asset,
            "title": title,
            "side": side,
            "value": value,
            "cur_price": cur_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": "",
            "token_id": asset,
        }

        # TAKE PROFIT: Subió +30%
        if pnl_pct >= TAKE_PROFIT_PCT:
            action["reason"] = f"TAKE PROFIT (+{pnl_pct:.0%})"
            return action

        # STOP LOSS: Bajó -40%
        if pnl_pct <= STOP_LOSS_PCT:
            action["reason"] = f"STOP LOSS ({pnl_pct:.0%})"
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
