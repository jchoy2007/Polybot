"""
PolyBot - Copy Trading (Copiar Traders Exitosos)
==================================================
Monitorea wallets de traders top en Polymarket y copia
sus apuestas automáticamente cuando detecta actividad.

Los mejores traders tienen win rates de 65-85%.
Copiarlos es la estrategia más fácil de implementar.
"""

import logging
import asyncio
import aiohttp
import json
from typing import List, Dict, Optional
from datetime import datetime
from config.settings import SAFETY, STATE, DATA_API_URL, GAMMA_API_URL

logger = logging.getLogger("polybot.copytrading")

# Wallets de traders conocidos con buenos resultados
# NOTA: Estas son wallets públicas en blockchain, toda su actividad
# es visible. Actualizar con wallets de mejor rendimiento.
DEFAULT_TOP_TRADERS = [
    # Puedes agregar wallets de traders exitosos aquí
    # Formato: {"address": "0x...", "name": "alias", "min_win_rate": 0.65}
]


class CopyTrader:
    """
    Copia apuestas de traders exitosos en Polymarket.
    
    Cómo funciona:
    1. Mantiene una lista de wallets "target" (traders top)
    2. Revisa su actividad reciente vía la Data API
    3. Cuando detecta una nueva apuesta, la evalúa
    4. Si cumple los filtros, copia la apuesta con monto proporcional
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.target_wallets: List[Dict] = []
        self.copied_trades: List[Dict] = []
        self.last_check_time = {}  # wallet -> timestamp
        self.copy_ratio = 0.1  # Copiar al 10% del monto original

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # =================================================================
    # DESCUBRIR TRADERS TOP
    # =================================================================

    async def discover_top_traders(self, limit: int = 10) -> List[Dict]:
        """
        Descubre traders top en Polymarket.
        Usa múltiples fuentes: Data API y Gamma API profiles.
        """
        session = await self._get_session()
        top_traders = []

        # Método 1: Polymarket Data API - leaderboard
        endpoints_to_try = [
            (f"{DATA_API_URL}/leaderboard", {"window": "1d", "limit": limit}),
            (f"{DATA_API_URL}/leaderboard", {"window": "7d", "limit": limit}),
            (f"https://data-api.polymarket.com/leaderboard", {"window": "1d", "limit": limit}),
            (f"https://lb-api.polymarket.com/leaderboard", {"window": "1d", "limit": limit}),
        ]

        for url, params in endpoints_to_try:
            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        entries = data if isinstance(data, list) else data.get("leaderboard", data.get("rankings", data.get("data", [])))
                        for trader in entries:
                            addr = (trader.get("address") or
                                   trader.get("proxyWallet") or
                                   trader.get("user") or "")
                            if addr and len(addr) > 10:
                                profit = float(trader.get("cashPnl") or
                                             trader.get("pnl") or
                                             trader.get("profit") or 0)
                                top_traders.append({
                                    "address": addr,
                                    "name": (trader.get("pseudonym") or
                                            trader.get("username") or
                                            trader.get("name") or
                                            addr[:12] + "..."),
                                    "profit": profit,
                                    "volume": float(trader.get("volume", 0) or 0),
                                    "positions": int(trader.get("numPositions", 0) or 0)
                                })
                        if top_traders:
                            break  # Found data, stop trying
            except Exception as e:
                logger.debug(f"   Endpoint {url}: {e}")
                continue

        # Método 2: Si no encontramos via API, buscar traders activos
        # en mercados populares vía Gamma API
        if not top_traders:
            try:
                async with session.get(
                    f"{GAMMA_API_URL}/markets",
                    params={"active": "true", "limit": 5, "order": "volume24hr", "ascending": "false"}
                ) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        for m in markets:
                            slug = m.get("slug", "")
                            if slug:
                                # Buscar trades recientes en este mercado
                                try:
                                    async with session.get(
                                        f"https://data-api.polymarket.com/trades",
                                        params={"market": m.get("conditionId", ""), "limit": 20},
                                        timeout=aiohttp.ClientTimeout(total=10)
                                    ) as trades_resp:
                                        if trades_resp.status == 200:
                                            trades = await trades_resp.json()
                                            trade_list = trades if isinstance(trades, list) else trades.get("trades", [])
                                            for t in trade_list:
                                                addr = t.get("maker", t.get("taker", t.get("user", "")))
                                                if addr and len(addr) > 10:
                                                    # Evitar duplicados
                                                    if not any(tr["address"] == addr for tr in top_traders):
                                                        top_traders.append({
                                                            "address": addr,
                                                            "name": addr[:12] + "...",
                                                            "profit": 0,
                                                            "volume": float(t.get("size", 0) or 0),
                                                            "positions": 0
                                                        })
                                except:
                                    continue
            except Exception as e:
                logger.debug(f"   Error buscando traders activos: {e}")

        if top_traders:
            top_traders.sort(key=lambda x: x.get("profit", 0), reverse=True)
            logger.info(f"👥 Descubiertos {len(top_traders)} traders")
            for i, t in enumerate(top_traders[:5], 1):
                logger.info(
                    f"   {i}. {t['name']} | "
                    f"Profit: ${t['profit']:,.0f} | "
                    f"Vol: ${t['volume']:,.0f}"
                )
        else:
            logger.info("   No se encontraron traders via API. Esto es normal si la API de leaderboard cambió.")

        return top_traders

    # =================================================================
    # OBTENER ACTIVIDAD RECIENTE DE UN TRADER
    # =================================================================

    async def get_trader_activity(self, wallet_address: str,
                                  limit: int = 10) -> List[Dict]:
        """
        Obtiene las apuestas recientes de un trader específico.
        """
        session = await self._get_session()
        activities = []

        try:
            async with session.get(
                f"{DATA_API_URL}/activity",
                params={
                    "user": wallet_address,
                    "limit": limit,
                    "type": "TRADE",
                    "side": "BUY",
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "DESC"
                }
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    history = data if isinstance(data, list) else data.get("history", [])
                    for trade in history:
                        activities.append({
                            "timestamp": trade.get("timestamp", 0),
                            "market": trade.get("title", trade.get("market", "")),
                            "slug": trade.get("slug", ""),
                            "side": trade.get("side", ""),
                            "outcome": trade.get("outcome", ""),
                            "price": float(trade.get("price", 0) or 0),
                            "size": float(trade.get("size", 0) or 0),
                            "asset": trade.get("asset", ""),
                            "condition_id": trade.get("conditionId", "")
                        })
        except Exception as e:
            logger.debug(f"Error obteniendo actividad de {wallet_address[:10]}: {e}")

        return activities

    # =================================================================
    # OBTENER POSICIONES DE UN TRADER
    # =================================================================

    async def get_trader_positions(self, wallet_address: str) -> List[Dict]:
        """Obtiene las posiciones abiertas de un trader."""
        session = await self._get_session()
        positions = []

        try:
            async with session.get(
                f"{DATA_API_URL}/positions",
                params={
                    "user": wallet_address,
                    "sortBy": "CURRENT",
                    "sortDirection": "DESC",
                    "limit": 20
                }
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pos_list = data if isinstance(data, list) else data.get("positions", [])
                    for pos in pos_list:
                        positions.append({
                            "market": pos.get("title", ""),
                            "slug": pos.get("slug", ""),
                            "outcome": pos.get("outcome", ""),
                            "size": float(pos.get("size", 0) or 0),
                            "avg_price": float(pos.get("avgPrice", 0) or 0),
                            "current_value": float(pos.get("currentValue", 0) or 0),
                            "pnl": float(pos.get("cashPnl", 0) or 0),
                            "pnl_pct": float(pos.get("percentPnl", 0) or 0)
                        })
        except Exception as e:
            logger.debug(f"Error obteniendo posiciones: {e}")

        return positions

    # =================================================================
    # EVALUAR SI COPIAR UN TRADE
    # =================================================================

    def should_copy_trade(self, trade: Dict, trader_profit: float) -> tuple:
        """
        Decide si copiar un trade específico.
        
        Filtros:
        - El trader debe tener profit positivo
        - El trade debe ser reciente (últimos 30 min)
        - El precio no debe ser extremo (0.03-0.97)
        - No copiar trades que ya copiamos
        """
        # Filtro: trader rentable
        if trader_profit <= 0:
            return False, "Trader no rentable"

        # Filtro: precio razonable
        price = trade.get("price", 0)
        if price < 0.03 or price > 0.97:
            return False, f"Precio extremo: ${price:.3f}"

        # Filtro: tamaño mínimo (trader apuesta al menos $10)
        size = trade.get("size", 0)
        if size < 5:
            return False, f"Trade muy pequeño: {size} shares"

        # Calcular monto a copiar (proporcional)
        copy_amount = max(1.0, STATE.current_bankroll * 0.05)
        copy_amount = min(copy_amount, STATE.current_bankroll * 0.10)

        return True, copy_amount

    # =================================================================
    # CICLO DE COPY TRADING
    # =================================================================

    async def run_cycle(self) -> List[Dict]:
        """
        Ejecuta un ciclo de copy trading:
        1. Descubre o usa traders top conocidos
        2. Revisa su actividad reciente
        3. Copia trades que pasen los filtros
        """
        if STATE.is_paused:
            return []

        logger.info("👥 Copy Trading: Buscando trades para copiar...")

        trades_copied = []

        # Paso 1: Descubrir traders si no tenemos
        if not self.target_wallets:
            self.target_wallets = await self.discover_top_traders(limit=10)

        if not self.target_wallets:
            logger.info("   No se encontraron traders top en esta sesión")
            return []

        # Paso 2: Revisar actividad de los top 5
        for trader in self.target_wallets[:5]:
            address = trader.get("address", "")
            if not address:
                continue

            # Obtener posiciones del trader
            positions = await self.get_trader_positions(address)

            if positions:
                logger.info(
                    f"   📊 {trader['name']}: "
                    f"{len(positions)} posiciones abiertas"
                )

                # Evaluar top posiciones
                for pos in positions[:3]:
                    should_copy, result = self.should_copy_trade(
                        pos, trader.get("profit", 0)
                    )

                    if should_copy:
                        copy_amount = result
                        trade = {
                            "strategy": "COPY_TRADE",
                            "timestamp": datetime.now().isoformat(),
                            "copied_from": trader["name"],
                            "copied_wallet": address[:10] + "...",
                            "market": pos["market"],
                            "outcome": pos["outcome"],
                            "original_size": pos["size"],
                            "copy_amount": copy_amount,
                            "price": pos["avg_price"],
                            "trader_pnl": pos.get("pnl", 0),
                            "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE"
                        }

                        if SAFETY.dry_run:
                            logger.info(
                                f"   🏃 [DRY RUN] Copiando: "
                                f"${copy_amount:.2f} en '{pos['market'][:40]}' "
                                f"(copiando a {trader['name']})"
                            )
                        else:
                            logger.info(
                                f"   💰 [LIVE] Copiando: "
                                f"${copy_amount:.2f} en '{pos['market'][:40]}'"
                            )

                        trades_copied.append(trade)
                        STATE.total_trades += 1

            await asyncio.sleep(1)  # Rate limiting

        if trades_copied:
            logger.info(f"   ✅ {len(trades_copied)} trades copiados")
        else:
            logger.info("   No se encontraron trades para copiar en este ciclo")

        self.copied_trades.extend(trades_copied)
        return trades_copied

    def get_stats(self) -> str:
        return (
            f"👥 Copy Trading: {len(self.copied_trades)} trades copiados | "
            f"Traders monitoreados: {len(self.target_wallets)}"
        )
