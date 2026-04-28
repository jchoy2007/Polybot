"""
PolyBot - Auto-Cobro Automático
================================
Cada ciclo revisa si hay mercados resueltos donde tenemos
tokens ganadores y los cobra automáticamente.

Funciona en 3 pasos:
1. Busca mercados cerrados recientes vía Gamma API
2. Verifica si tenemos tokens (balance > 0) en el CTF contract
3. Ejecuta redeemPositions para cobrar USDC.e

Se integra al loop principal del bot.
"""

import os
import json
import time
import logging
import asyncio
from typing import Optional, Dict, List, Tuple
from datetime import datetime

import aiohttp

from config.settings import STATE

logger = logging.getLogger("polybot.redeem")

# Contratos de Polymarket en Polygon
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # Polymarket v2 collateral
WCOL_ADDRESS = "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2"  # WrappedCollateral
GAMMA_API_URL = "https://gamma-api.polymarket.com"

# ABI mínimas
CTF_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"}
        ],
        "name": "redeemPositions",
        "outputs": [],
        "type": "function"
    },
    {
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"}
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "inputs": [{"name": "conditionId", "type": "bytes32"}],
        "name": "payoutDenominator",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    }
]

NEG_RISK_ABI = [
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "amounts", "type": "uint256[]"}
        ],
        "name": "redeemPositions",
        "outputs": [],
        "type": "function"
    }
]

ERC20_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    }
]

WCOL_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "inputs": [{"name": "_to", "type": "address"}, {"name": "_amount", "type": "uint256"}],
        "name": "unwrap",
        "outputs": [],
        "type": "function"
    }
]


class AutoRedeemer:
    """
    Cobra automáticamente ganancias de mercados resueltos.
    Se ejecuta una vez por ciclo del bot (cada 15 min).
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.w3 = None
        self.account = None
        self.address = None
        self.ctf_contract = None
        self.usdc_contract = None

        # Stats
        self.total_redeemed = 0.0
        self.redeem_count = 0
        self.last_redeem_time = 0
        self.min_redeem_interval = 300  # Mínimo 5 min entre intentos

        # Cache de mercados ya cobrados (evitar reintentos)
        self.redeemed_markets: set = set()

        # Inicializar Web3
        self._init_web3()

    def _init_web3(self):
        """Inicializa conexión a Polygon."""
        try:
            from web3 import Web3

            pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if not pk or pk == "0x...":
                logger.debug("   Auto-redeem: clave privada no configurada")
                return

            rpcs = [
                "https://polygon-bor-rpc.publicnode.com",
                "https://1rpc.io/matic",
                "https://polygon.meowrpc.com",
                "https://polygon.drpc.org",
                "https://polygon-rpc.com",
                "https://rpc.ankr.com/polygon",
                "https://polygon.llamarpc.com",
            ]

            for rpc in rpcs:
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
                    if w3.is_connected():
                        self.w3 = w3
                        break
                except:
                    continue

            if not self.w3:
                logger.warning("   Auto-redeem: no se pudo conectar a Polygon")
                return

            self.account = self.w3.eth.account.from_key(pk)
            self.address = self.account.address
            funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
            self.balance_target = self.w3.to_checksum_address(funder) if funder else self.address

            self.ctf_contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(CTF_ADDRESS),
                abi=CTF_ABI
            )

            self.usdc_contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(PUSD_ADDRESS),
                abi=ERC20_ABI
            )

            logger.info("   ✅ Auto-redeem inicializado")

        except Exception as e:
            logger.warning(f"   Auto-redeem init error: {str(e)[:60]}")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    # =================================================================
    # BUSCAR MERCADOS RESUELTOS DONDE TENEMOS TOKENS
    # =================================================================
    async def find_redeemable_markets(self) -> List[Dict]:
        """
        Busca mercados cerrados y resueltos donde podríamos
        tener tokens para cobrar.
        """
        session = await self._get_session()
        redeemable = []

        try:
            # Obtener mercados cerrados recientemente
            async with session.get(
                f"{GAMMA_API_URL}/markets",
                params={
                    "limit": 100,
                    "closed": "true",
                    "order": "endDate",
                    "ascending": "false"
                }
            ) as resp:
                if resp.status != 200:
                    return []

                markets = await resp.json()

                for m in markets:
                    market_id = m.get("id", "")

                    # Saltar si ya lo cobramos
                    if market_id in self.redeemed_markets:
                        continue

                    condition_id = m.get("conditionId", "")
                    if not condition_id:
                        continue

                    # Obtener token IDs
                    tokens_str = m.get("clobTokenIds", "[]")
                    if isinstance(tokens_str, str):
                        try:
                            tokens = json.loads(tokens_str)
                        except:
                            continue
                    else:
                        tokens = tokens_str

                    if not tokens:
                        continue

                    # Verificar si tenemos tokens en este mercado
                    has_tokens = False
                    token_balances = []

                    for i, token_id in enumerate(tokens):
                        try:
                            token_int = int(token_id) if isinstance(token_id, str) else token_id
                            balance = self.ctf_contract.functions.balanceOf(
                                self.address, token_int
                            ).call()
                            token_balances.append(balance)
                            if balance > 0:
                                has_tokens = True
                        except:
                            token_balances.append(0)

                    if has_tokens:
                        redeemable.append({
                            "market_id": market_id,
                            "question": m.get("question", ""),
                            "condition_id": condition_id,
                            "neg_risk": m.get("negRisk", False),
                            "tokens": tokens,
                            "token_balances": token_balances,
                            "resolved": m.get("resolved", False)
                        })

        except Exception as e:
            logger.debug(f"   Error buscando mercados: {str(e)[:60]}")

        return redeemable

    # =================================================================
    # COBRAR UN MERCADO
    # =================================================================
    def redeem_market(self, market_info: Dict) -> Tuple[bool, float]:
        """
        Intenta cobrar tokens de un mercado resuelto.
        v10: Para neg_risk usa WCOL como collateral + unwrap a USDC.e.
        Retorna (éxito, monto_cobrado).
        """
        if not self.w3 or not self.account:
            return False, 0.0

        pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
        if not pk:
            return False, 0.0

        cid = market_info["condition_id"]
        neg_risk = market_info["neg_risk"]

        try:
            cid_bytes = bytes.fromhex(cid[2:]) if cid.startswith("0x") else bytes.fromhex(cid)
            payout = self.ctf_contract.functions.payoutDenominator(cid_bytes).call()

            if payout == 0:
                logger.debug(f"   Mercado aún no resuelto por oráculo")
                return False, 0.0

            balance_before = self.usdc_contract.functions.balanceOf(self.balance_target).call()

            wcol_contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(WCOL_ADDRESS),
                abi=WCOL_ABI
            )

            if neg_risk:
                # v10 FIX: Para neg_risk, usar WrappedCollateral como collateral
                wcol_before = wcol_contract.functions.balanceOf(self.address).call()

                time.sleep(3)  # Esperar nonce
                nonce = self.w3.eth.get_transaction_count(self.address, 'pending')
                txn = self.ctf_contract.functions.redeemPositions(
                    self.w3.to_checksum_address(WCOL_ADDRESS),
                    bytes.fromhex("00" * 32),
                    cid_bytes,
                    [1, 2]
                ).build_transaction({
                    'from': self.address, 'nonce': nonce, 'gas': 500000,
                    'gasPrice': int(self.w3.eth.gas_price * 1.2), 'chainId': 137
                })

                signed = self.w3.eth.account.sign_transaction(txn, pk)
                tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                time.sleep(3)

                if receipt.status != 1:
                    logger.warning(f"   WCOL redeem TX fallo")
                    return False, 0.0

                wcol_after = wcol_contract.functions.balanceOf(self.address).call()
                wcol_gained = wcol_after - wcol_before

                if wcol_gained > 0:
                    # Unwrap WCOL → USDC.e
                    try:
                        time.sleep(3)
                        nonce2 = self.w3.eth.get_transaction_count(self.address, 'pending')
                        txn2 = wcol_contract.functions.unwrap(
                            self.address, wcol_gained
                        ).build_transaction({
                            'from': self.address, 'nonce': nonce2, 'gas': 200000,
                            'gasPrice': int(self.w3.eth.gas_price * 1.2), 'chainId': 137
                        })
                        signed2 = self.w3.eth.account.sign_transaction(txn2, pk)
                        tx_hash2 = self.w3.eth.send_raw_transaction(signed2.raw_transaction)
                        self.w3.eth.wait_for_transaction_receipt(tx_hash2, timeout=60)
                        time.sleep(3)
                    except Exception as e:
                        logger.warning(f"   Unwrap fallo: {str(e)[:40]}")

                balance_after = self.usdc_contract.functions.balanceOf(self.balance_target).call()
                gained = (balance_after - balance_before) / 1e6
                if gained > 0:
                    logger.info(f"   COBRADO ${gained:.2f} (neg_risk/WCOL)")
                return True, gained

            else:
                # Mercado estándar CTF → USDC.e directo
                time.sleep(3)
                nonce = self.w3.eth.get_transaction_count(self.address, 'pending')
                txn = self.ctf_contract.functions.redeemPositions(
                    self.w3.to_checksum_address(PUSD_ADDRESS),
                    bytes.fromhex("00" * 32),
                    cid_bytes,
                    [1, 2]
                ).build_transaction({
                    'from': self.address, 'nonce': nonce, 'gas': 500000,
                    'gasPrice': int(self.w3.eth.gas_price * 1.2), 'chainId': 137
                })

                signed = self.w3.eth.account.sign_transaction(txn, pk)
                tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

                if receipt.status == 1:
                    time.sleep(3)
                    balance_after = self.usdc_contract.functions.balanceOf(self.balance_target).call()
                    gained = (balance_after - balance_before) / 1e6
                    if gained > 0:
                        logger.info(f"   COBRADO ${gained:.2f} (CTF directo)")
                    return True, gained
                else:
                    logger.warning(f"   ❌ TX falló")
                    return False, 0.0

        except Exception as e:
            error_msg = str(e)[:80]
            if "execution reverted" in error_msg.lower():
                logger.debug(f"   Reverted (ya cobrado o sin ganancia): {error_msg[:40]}")
            else:
                logger.warning(f"   Error redeem: {error_msg}")
            return False, 0.0

    # =================================================================
    # CICLO DE AUTO-COBRO
    # =================================================================
    async def run_cycle(self) -> Dict:
        """
        Ciclo completo de auto-cobro.
        Retorna resumen de lo cobrado.
        """
        now = time.time()

        # Respetar intervalo mínimo
        if now - self.last_redeem_time < self.min_redeem_interval:
            return {"checked": 0, "redeemed": 0, "amount": 0.0}

        self.last_redeem_time = now

        if not self.w3 or not self.account:
            self._init_web3()
            if not self.w3:
                return {"checked": 0, "redeemed": 0, "amount": 0.0}

        logger.info("   🔍 Buscando posiciones por cobrar...")

        # Buscar mercados donde tenemos tokens
        redeemable = await self.find_redeemable_markets()

        if not redeemable:
            logger.info("   No se encontraron posiciones por cobrar")
            return {"checked": 0, "redeemed": 0, "amount": 0.0}

        logger.info(f"   Encontradas {len(redeemable)} posiciones con tokens")

        total_gained = 0.0
        total_redeemed = 0

        for market_info in redeemable:
            question = market_info["question"][:50]

            # Mostrar balances de tokens
            balances_str = ", ".join(
                f"${b / 1e6:.2f}" for b in market_info["token_balances"] if b > 0
            )
            logger.info(f"   🔄 Cobrando: {question}... (tokens: {balances_str})")

            success, gained = self.redeem_market(market_info)

            if success:
                total_gained += gained
                total_redeemed += 1
                self.redeemed_markets.add(market_info["market_id"])
                self.total_redeemed += gained
                self.redeem_count += 1

                # Actualizar bankroll
                STATE.current_bankroll += gained
            else:
                # Marcar como intentado para no reintentar inmediatamente
                # (se reintentará en el próximo ciclo)
                pass

        result = {
            "checked": len(redeemable),
            "redeemed": total_redeemed,
            "amount": total_gained
        }

        if total_redeemed > 0:
            logger.info(
                f"   💰 Auto-cobro: {total_redeemed} posiciones cobradas, "
                f"${total_gained:.2f} USDC.e recuperados | "
                f"Bankroll: ${STATE.current_bankroll:.2f}"
            )
        else:
            logger.info("   Sin posiciones listas para cobrar (oráculo pendiente)")

        return result

    def get_stats(self) -> str:
        return (
            f"💰 Auto-cobro: {self.redeem_count} cobrados | "
            f"Total: ${self.total_redeemed:.2f}"
        )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
