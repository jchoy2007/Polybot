"""
PolyBot - Ejecutor de Trades (REAL)
======================================
Ejecuta apuestas REALES en Polymarket vía py-clob-client.
"""

import json
import os
import time
import logging
from typing import Optional, Dict
from datetime import datetime
from config.settings import (
    SAFETY, STATE,
    POLYGON_WALLET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS, CLOB_API_URL
)
from core.risk_manager import RiskManager
from core.ai_analyzer import MarketAnalysis

logger = logging.getLogger("polybot.executor")


class TradeExecutor:
    """Ejecuta trades en Polymarket."""

    BETS_FILE = "data/bets_placed.json"

    def __init__(self, risk_manager: RiskManager):
        self.risk = risk_manager
        self.pending_orders = []
        self.executed_orders = []
        self.clob_client = None
        self.markets_bet_on = self._load_bets_history()

    def _load_bets_history(self) -> set:
        """Carga historial de apuestas para no duplicar al reiniciar."""
        try:
            os.makedirs("data", exist_ok=True)
            with open(self.BETS_FILE, "r") as f:
                data = json.load(f)
                ids = set(data.get("market_ids", []))
                if ids:
                    logger.info(f"   📂 Cargadas {len(ids)} apuestas previas (anti-duplicado)")
                return ids
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def _save_bet(self, market_id: str, question: str = ""):
        """Guarda apuesta en disco para persistencia."""
        try:
            os.makedirs("data", exist_ok=True)
            try:
                with open(self.BETS_FILE, "r") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {"market_ids": [], "history": []}

            if market_id not in data["market_ids"]:
                data["market_ids"].append(market_id)
                data["history"].append({
                    "market_id": market_id,
                    "question": question,
                    "timestamp": datetime.now().isoformat()
                })

            with open(self.BETS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"Error guardando bet: {e}")

    def init_live_client(self):
        """Inicializa el cliente CLOB para órdenes reales.
        Prueba múltiples configuraciones de firma hasta encontrar la que
        ve balance disponible.
        """
        pk = POLYGON_WALLET_PRIVATE_KEY
        if not pk or pk == "0x...":
            logger.warning("Clave privada no configurada.")
            return False

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

            pk_clean = pk[2:] if pk.startswith("0x") else pk
            funder = POLYMARKET_FUNDER_ADDRESS

            # Probar configuraciones en orden de probabilidad
            configs = []
            
            # Config 1: EOA directo sin proxy (fondos en wallet directamente)
            configs.append({"sig_type": 0, "funder": None, "label": "EOA directo"})
            
            # Config 2: Browser proxy (MetaMask)
            if funder:
                configs.append({"sig_type": 2, "funder": funder, "label": "Browser proxy"})
                configs.append({"sig_type": 1, "funder": funder, "label": "Email/Magic proxy"})
                configs.append({"sig_type": 0, "funder": funder, "label": "EOA + funder"})
            
            # Si hay SIGNATURE_TYPE forzado en .env, probarlo primero
            forced = os.environ.get("SIGNATURE_TYPE")
            if forced is not None:
                forced_int = int(forced)
                configs.insert(0, {
                    "sig_type": forced_int,
                    "funder": funder if forced_int > 0 else None,
                    "label": f"Forzado sig_type={forced_int}"
                })

            for cfg in configs:
                try:
                    if cfg["funder"]:
                        client = ClobClient(
                            host="https://clob.polymarket.com",
                            key=pk_clean, chain_id=137,
                            signature_type=cfg["sig_type"],
                            funder=cfg["funder"]
                        )
                    else:
                        client = ClobClient(
                            host="https://clob.polymarket.com",
                            key=pk_clean, chain_id=137,
                            signature_type=cfg["sig_type"]
                        )

                    creds = client.create_or_derive_api_creds()
                    client.set_api_creds(creds)

                    # Verificar balance
                    bal_resp = client.get_balance_allowance(
                        params=BalanceAllowanceParams(
                            asset_type=AssetType.COLLATERAL,
                            signature_type=cfg["sig_type"]
                        )
                    )
                    balance = float(bal_resp.get("balance", "0")) / 1e6  # USDC tiene 6 decimales

                    logger.info(
                        f"   🔍 {cfg['label']}: balance=${balance:.2f}"
                    )

                    if balance > 0:
                        self.clob_client = client
                        logger.info(
                            f"✅ Cliente CLOB listo ({cfg['label']}) "
                            f"| Balance: ${balance:.2f}"
                        )
                        return True

                except Exception as e:
                    logger.debug(f"   {cfg['label']}: {str(e)[:60]}")
                    continue

            # Si ninguna config encuentra balance, usar la primera que no dio error
            logger.warning("⚠️ Ninguna config encontró balance > 0, usando EOA directo")
            client = ClobClient(
                host="https://clob.polymarket.com",
                key=pk_clean, chain_id=137, signature_type=0
            )
            client.set_api_creds(client.create_or_derive_api_creds())
            self.clob_client = client
            logger.info("✅ Cliente CLOB listo (fallback EOA)")
            return True

        except Exception as e:
            logger.error(f"Error inicializando CLOB: {e}")
            return False

    async def execute_bet(self, analysis: MarketAnalysis,
                          bet_amount: float) -> Dict:
        # Verificar si ya apostamos en este mercado
        if analysis.market_id in self.markets_bet_on:
            logger.info(f"   ⏭️ Skip: ya apostamos en {analysis.question[:40]}")
            return {"status": "SKIPPED_DUPLICATE", "question": analysis.question}

        order_info = {
            "timestamp": datetime.now().isoformat(),
            "market_id": analysis.market_id,
            "question": analysis.question,
            "side": analysis.side,
            "amount_usd": bet_amount,
            "price": analysis.market_price,
            "shares": round(bet_amount / analysis.market_price, 4),
            "estimated_prob": analysis.estimated_probability,
            "edge": analysis.edge,
            "confidence": analysis.confidence,
            "reasoning": analysis.reasoning,
            "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE"
        }

        if SAFETY.dry_run:
            return await self._execute_dry_run(order_info)
        else:
            return await self._execute_live(order_info, analysis)

    async def _execute_dry_run(self, order: Dict) -> Dict:
        logger.info(
            f"🏃 [DRY RUN] Apuesta simulada:\n"
            f"   Mercado: {order['question'][:60]}\n"
            f"   Lado: {order['side']}\n"
            f"   Monto: ${order['amount_usd']:.2f}\n"
            f"   Precio: ${order['price']:.3f}\n"
            f"   Shares: {order['shares']}\n"
            f"   Edge: {order['edge']:.1%}\n"
            f"   Confianza: {order['confidence']:.1%}"
        )

        order["status"] = "SIMULATED"
        order["order_id"] = f"DRY_{int(time.time())}"
        self.executed_orders.append(order)
        self.markets_bet_on.add(order["market_id"])
        STATE.open_positions += 1
        STATE.total_trades += 1

        self.risk.record_trade(
            market_id=order["market_id"],
            market_question=order["question"],
            side=order["side"],
            amount=order["amount_usd"],
            price=order["price"],
            estimated_prob=order["estimated_prob"],
            edge=order["edge"]
        )
        return order

    async def _execute_live(self, order: Dict, analysis: MarketAnalysis) -> Dict:
        """Ejecuta una orden REAL en Polymarket."""

        # Inicializar cliente si no está listo
        if not self.clob_client:
            if not self.init_live_client():
                order["status"] = "ERROR_NO_CLIENT"
                self.executed_orders.append(order)
                return order

        logger.info(
            f"💰 [LIVE] Ejecutando apuesta REAL:\n"
            f"   Mercado: {order['question'][:60]}\n"
            f"   Lado: {order['side']}\n"
            f"   Monto: ${order['amount_usd']:.2f}\n"
            f"   Precio: ${order['price']:.3f}"
        )

        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            # Obtener token_id del mercado
            token_id = await self._get_token_id(analysis)

            if not token_id:
                logger.error("❌ No se pudo obtener token_id")
                order["status"] = "ERROR_NO_TOKEN"
                self.executed_orders.append(order)
                return order

            logger.info(f"   Token ID: {token_id[:20]}...")

            # Intentar orden de mercado (FOK)
            try:
                logger.info(f"   Enviando orden de mercado: ${order['amount_usd']:.2f}...")

                market_order = MarketOrderArgs(
                    token_id=token_id,
                    amount=order['amount_usd'],
                    side=BUY
                )

                signed_order = self.clob_client.create_market_order(market_order)
                response = self.clob_client.post_order(signed_order, OrderType.FOK)

                logger.info(f"   Respuesta: {response}")

                if response and isinstance(response, dict):
                    order_id = response.get("orderID", response.get("id", ""))
                    success = response.get("success", False)
                    status = response.get("status", "")

                    if success or order_id or status == "matched":
                        order["status"] = "EXECUTED"
                        order["order_id"] = order_id
                        self.markets_bet_on.add(order["market_id"])
                        self._save_bet(order["market_id"], order.get("question", ""))
                        STATE.current_bankroll -= order["amount_usd"]
                        logger.info(f"   ✅ ¡ORDEN EJECUTADA! ID: {order_id}")
                        logger.info(f"   💰 Capital restante: ${STATE.current_bankroll:.2f}")
                        STATE.open_positions += 1
                        STATE.total_trades += 1
                    else:
                        # Intentar orden límite
                        logger.info("   Orden de mercado no llenó, intentando límite...")
                        order = self._place_limit_order(
                            token_id, order, BUY, OrderArgs, OrderType
                        )
                else:
                    order = self._place_limit_order(
                        token_id, order, BUY, OrderArgs, OrderType
                    )

            except Exception as e:
                logger.warning(f"   Error orden de mercado: {e}")
                logger.info("   Intentando orden límite...")
                try:
                    order = self._place_limit_order(
                        token_id, order, BUY, OrderArgs, OrderType
                    )
                except Exception as e2:
                    logger.error(f"   Error orden límite: {e2}")
                    order["status"] = "ERROR"
                    order["error"] = str(e2)

        except Exception as e:
            logger.error(f"❌ Error: {e}")
            order["status"] = "ERROR"
            order["error"] = str(e)

        self.executed_orders.append(order)

        self.risk.record_trade(
            market_id=order["market_id"],
            market_question=order["question"],
            side=order["side"],
            amount=order["amount_usd"],
            price=order["price"],
            estimated_prob=order["estimated_prob"],
            edge=order["edge"]
        )
        return order

    def _place_limit_order(self, token_id, order, BUY, OrderArgs, OrderType):
        """Coloca una orden límite GTC."""
        # Obtener tick size del mercado
        try:
            tick_size = "0.01"  # Default
            neg_risk = False
            limit_order = OrderArgs(
                token_id=token_id,
                price=round(order["price"], 2),
                size=order["shares"],
                side=BUY
            )

            signed = self.clob_client.create_order(limit_order)
            response = self.clob_client.post_order(signed, OrderType.GTC)

            logger.info(f"   Respuesta límite: {response}")

            if response and isinstance(response, dict):
                order_id = response.get("orderID", response.get("id", ""))
                if order_id or response.get("success"):
                    order["status"] = "LIMIT_PLACED"
                    order["order_id"] = order_id
                    self.markets_bet_on.add(order["market_id"])
                    self._save_bet(order["market_id"], order.get("question", ""))
                    STATE.current_bankroll -= order["amount_usd"]
                    logger.info(f"   ✅ Orden límite colocada: {order_id}")
                    logger.info(f"   💰 Capital restante: ${STATE.current_bankroll:.2f}")
                    STATE.open_positions += 1
                    STATE.total_trades += 1
                else:
                    order["status"] = "FAILED"
                    order["error"] = str(response)
            else:
                order["status"] = "FAILED"

        except Exception as e:
            order["status"] = "ERROR_LIMIT"
            order["error"] = str(e)
            logger.error(f"   Error límite: {e}")

        return order

    async def _get_token_id(self, analysis: MarketAnalysis) -> Optional[str]:
        """Obtiene el token_id del mercado."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                # Buscar por ID
                url = f"https://gamma-api.polymarket.com/markets"
                async with session.get(
                    url, params={"id": analysis.market_id}
                ) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        if markets:
                            market = markets[0] if isinstance(markets, list) else markets
                        else:
                            return None
                    else:
                        return None

                # Extraer token IDs
                tokens_str = market.get("clobTokenIds", "")
                if isinstance(tokens_str, str):
                    tokens = json.loads(tokens_str)
                elif isinstance(tokens_str, list):
                    tokens = tokens_str
                else:
                    return None

                if len(tokens) < 2:
                    return None

                # YES = tokens[0], NO = tokens[1]
                if analysis.side.upper() in ("YES", "UP"):
                    return tokens[0]
                else:
                    return tokens[1]

        except Exception as e:
            logger.error(f"Error obteniendo token_id: {e}")
            return None

    def get_execution_summary(self) -> str:
        if not self.executed_orders:
            return "📭 No se ejecutaron órdenes en esta sesión."

        lines = ["📋 RESUMEN DE ÓRDENES", "=" * 50]

        for i, order in enumerate(self.executed_orders, 1):
            lines.append(
                f"\n{i}. [{order['mode']}] {order['side']} "
                f"${order['amount_usd']:.2f}\n"
                f"   Mercado: {order['question'][:55]}\n"
                f"   Precio: ${order['price']:.3f} | "
                f"Edge: {order['edge']:.1%} | "
                f"Estado: {order['status']}"
            )

        lines.append(f"\n{'=' * 50}")
        lines.append(f"Total órdenes: {len(self.executed_orders)}")
        lines.append(f"Bankroll: ${STATE.current_bankroll:.2f}")

        return "\n".join(lines)
