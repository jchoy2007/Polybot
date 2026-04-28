"""
PolyBot - COBRAR GANANCIAS (Redeem v10)
=========================================
Fix DEFINITIVO para neg_risk markets:
1. Usa WrappedCollateral como collateral (no USDC.e)
2. Unwrap WrappedCollateral → USDC.e despues del redeem

USO: python redeem.py
"""

import os, sys, json, asyncio, time, aiohttp
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
WCOL_ADDRESS = "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2"  # WrappedCollateral
DATA_API_URL = "https://data-api.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"

UMA_ADAPTER_ABI = [{
    "inputs":[{"name":"questionID","type":"bytes32"}],
    "name":"resolve","outputs":[],"stateMutability":"nonpayable","type":"function"
}]

CTF_ABI = [
    {"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"type":"function"},
    {"inputs":[{"name":"account","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"conditionId","type":"bytes32"}],"name":"payoutDenominator","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"operator","type":"address"}],"name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"type":"function"},
]
NEG_RISK_ABI = [
    {"inputs":[{"name":"conditionId","type":"bytes32"},{"name":"amounts","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"type":"function"},
]
ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
]
WCOL_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"_to","type":"address"},{"name":"_amount","type":"uint256"}],"name":"unwrap","outputs":[],"type":"function"},
]

def connect_polygon():
    # Priorizar Alchemy RPC si está configurado (más rápido y confiable)
    alchemy = os.getenv("ALCHEMY_RPC_URL", "")
    rpcs = []
    if alchemy:
        rpcs.append(alchemy)
    rpcs.extend([
        "https://polygon-bor-rpc.publicnode.com",
        "https://1rpc.io/matic",
        "https://polygon-rpc.com"
    ])
    for rpc in rpcs:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
            if w3.is_connected():
                if rpc == alchemy:
                    print(f"  Conectado via Alchemy RPC")
                return w3
        except:
            continue
    return None

async def get_market_resolver(condition_id):
    """Fetch (questionID, resolvedBy adapter) for a condition_id from Gamma API.
    Returns (None, None) if not found or on error."""
    url = f"{GAMMA_API_URL}/markets?condition_ids={condition_id}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None, None
                data = await resp.json()
                if not data or not isinstance(data, list) or len(data) == 0:
                    return None, None
                m = data[0]
                return m.get("questionID"), m.get("resolvedBy")
    except Exception:
        return None, None

def try_uma_resolve(w3, eoa, pk, question_id, adapter_addr, title_short):
    """Call resolve(questionID) on the UMA adapter to unstick a decided market.
    Simulates first to avoid burning gas on a guaranteed revert. Returns True on success."""
    try:
        adapter = w3.eth.contract(
            address=w3.to_checksum_address(adapter_addr),
            abi=UMA_ADAPTER_ABI,
        )
        qid_bytes = bytes.fromhex(question_id[2:] if question_id.startswith("0x") else question_id)
        try:
            adapter.functions.resolve(qid_bytes).call({"from": eoa})
        except Exception as e:
            print(f"  resolve() sim fallo: {str(e)[:60]} | {title_short}")
            return False
        nonce = w3.eth.get_transaction_count(eoa, "pending")
        txn = adapter.functions.resolve(qid_bytes).build_transaction({
            "from": eoa, "nonce": nonce, "gas": 600000,
            "gasPrice": int(w3.eth.gas_price * 1.2), "chainId": 137,
        })
        signed = w3.eth.account.sign_transaction(txn, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status == 1:
            print(f"  resolve() OK gas={receipt.gasUsed} | {title_short}")
            return True
        print(f"  resolve() tx status=0 | {title_short}")
        return False
    except Exception as e:
        print(f"  resolve() error: {str(e)[:80]} | {title_short}")
        return False

async def find_all_positions(address):
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    seen = set()
    all_positions = []
    for addr in [funder, address]:
        if not addr: continue
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.get(f"{DATA_API_URL}/positions?user={addr.lower()}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list):
                            for p in data:
                                key = (p.get("conditionId",""), p.get("asset",""))
                                if key in seen:
                                    continue
                                seen.add(key)
                                all_positions.append(p)
        except: continue
    return all_positions

async def redeem_all():
    print(f"\n{'='*60}")
    print(f"  PolyBot - COBRAR GANANCIAS v10")
    print(f"{'='*60}")

    w3 = connect_polygon()
    if not w3:
        print("  Error: No se pudo conectar")
        return

    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    eoa = w3.eth.account.from_key(pk).address
    ctf = w3.eth.contract(address=w3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    neg = w3.eth.contract(address=w3.to_checksum_address(NEG_RISK_ADAPTER), abi=NEG_RISK_ABI)
    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E_ADDRESS), abi=ERC20_ABI)
    wcol = w3.eth.contract(address=w3.to_checksum_address(WCOL_ADDRESS), abi=WCOL_ABI)

    bal_start = usdc.functions.balanceOf(eoa).call() / 1e6
    wcol_start = wcol.functions.balanceOf(eoa).call() / 1e6
    print(f"  Wallet: {eoa}")
    print(f"  USDC.e: ${bal_start:.2f}")
    print(f"  WrappedCol: ${wcol_start:.6f}")

    # Obtener posiciones
    positions = await find_all_positions(eoa)
    if not positions:
        print("  No se encontraron posiciones")
        return
    print(f"  {len(positions)} posiciones encontradas\n")

    to_redeem = []
    pending = []
    pending_positions = []  # Posiciones que el oráculo no reportó (para vender como fallback)

    for pos in positions:
        title = pos.get("title") or pos.get("question") or "?"
        condition_id = pos.get("conditionId") or ""
        asset = pos.get("asset") or ""
        size = float(pos.get("size") or 0)
        cur_price = float(pos.get("curPrice") or 0)
        cur_value = float(pos.get("currentValue") or 0)
        side = pos.get("outcome") or "?"

        if not condition_id or size <= 0:
            continue

        token_bal = 0
        if asset:
            try:
                token_bal = ctf.functions.balanceOf(eoa, int(asset)).call()
            except: pass
        if token_bal <= 0:
            continue

        try:
            cid_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
            payout_denom = ctf.functions.payoutDenominator(cid_bytes).call()

            # Auto-resolve: si el mercado esta claramente decidido (>=95%) pero
            # el oraculo no ha reportado, intentar llamar resolve() en el adapter
            # para desbloquearlo antes de marcarlo como pending.
            if payout_denom == 0 and cur_price >= 0.95:
                q_id, adapter_addr = await get_market_resolver(condition_id)
                if q_id and adapter_addr:
                    print(f"  auto-resolve() | {str(title)[:45]}")
                    if try_uma_resolve(w3, eoa, pk, q_id, adapter_addr, str(title)[:45]):
                        time.sleep(10)
                        try:
                            payout_denom = ctf.functions.payoutDenominator(cid_bytes).call()
                        except:
                            pass

            if payout_denom == 0:
                pending.append(f"  ... {str(title)[:45]} | {side} | ${cur_value:.2f} ({cur_price:.0%})")
                # Guardar datos para venta fallback
                if cur_value > 0.50:
                    pending_positions.append({
                        "title": str(title)[:55],
                        "cid_bytes": cid_bytes,
                        "asset": asset,
                        "token_bal": token_bal,
                        "is_win": cur_price >= 0.50,
                        "side": side,
                        "cur_price": cur_price,
                    })
                continue
        except:
            pending.append(f"  ... {str(title)[:45]} | {side} | ${cur_value:.2f}")
            continue

        is_win = cur_price >= 0.50
        tag = "WIN" if is_win else "LOSS"
        print(f"  [{tag}] {str(title)[:45]} | {token_bal/1e6:.2f} tokens")

        to_redeem.append({
            "title": str(title)[:55],
            "cid_bytes": cid_bytes,
            "asset": asset,
            "token_bal": token_bal,
            "is_win": is_win,
            "side": side,
            "cur_price": cur_price,
        })

    if pending:
        print(f"\n  Pendientes oráculo ({len(pending_positions)}):")
        for p in pending:
            print(p)
        # NO vender posiciones pendientes de oráculo.
        # El oráculo puede tardar horas en reportar. Las posiciones
        # activas (partidos en progreso) también aparecen como
        # "pendientes" y venderlas causa pérdidas innecesarias.
        # Solo cobramos posiciones donde el oráculo YA reportó.
        if not to_redeem:
            print(f"\n  Esperando a que el oráculo reporte. No se vende nada.")
            return

    wins = sum(1 for r in to_redeem if r["is_win"])
    losses = sum(1 for r in to_redeem if not r["is_win"])
    print(f"\n  Cobrando {len(to_redeem)} ({wins} WIN, {losses} LOSS)...\n")

    redeemed = 0

    for pos in to_redeem:
        title = pos["title"]
        cid_bytes = pos["cid_bytes"]
        is_win = pos["is_win"]

        usdc_pre = usdc.functions.balanceOf(eoa).call() / 1e6
        wcol_pre = wcol.functions.balanceOf(eoa).call()
        success = False

        # Esperar a que el nonce se actualice (evita "replacement transaction underpriced")
        time.sleep(3)

        # METODO 1: CTF redeem con WrappedCollateral como collateral
        try:
            nonce = w3.eth.get_transaction_count(eoa, 'pending')
            txn = ctf.functions.redeemPositions(
                w3.to_checksum_address(WCOL_ADDRESS),  # WrappedCollateral, NO USDC.e
                bytes.fromhex("00" * 32),
                cid_bytes,
                [1, 2]
            ).build_transaction({
                'from': eoa, 'nonce': nonce, 'gas': 500000,
                'gasPrice': int(w3.eth.gas_price * 1.2), 'chainId': 137
            })
            signed = w3.eth.account.sign_transaction(txn, pk)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            time.sleep(2)

            wcol_post = wcol.functions.balanceOf(eoa).call()
            wcol_gained = wcol_post - wcol_pre

            if receipt.status == 1 and wcol_gained > 0:
                # Unwrap WrappedCollateral → USDC.e
                try:
                    time.sleep(3)
                    nonce2 = w3.eth.get_transaction_count(eoa, 'pending')
                    txn2 = wcol.functions.unwrap(eoa, wcol_gained).build_transaction({
                        'from': eoa, 'nonce': nonce2, 'gas': 200000,
                        'gasPrice': int(w3.eth.gas_price * 1.2), 'chainId': 137
                    })
                    signed2 = w3.eth.account.sign_transaction(txn2, pk)
                    tx_hash2 = w3.eth.send_raw_transaction(signed2.raw_transaction)
                    receipt2 = w3.eth.wait_for_transaction_receipt(tx_hash2, timeout=60)
                    time.sleep(2)

                    usdc_post = usdc.functions.balanceOf(eoa).call() / 1e6
                    diff = round(usdc_post - usdc_pre, 2)
                    if diff >= 0.01:
                        success = True
                        print(f"  +${diff:.2f} WIN (WCOL+Unwrap) | {title}")
                    else:
                        success = True
                        print(f"  $0.00 (WCOL redeem OK, unwrap=${diff:.2f}) | {title}")
                except Exception as e:
                    # Redeem funciono pero unwrap fallo
                    success = True
                    print(f"  WCOL +{wcol_gained/1e6:.2f} (unwrap fallo: {str(e)[:40]}) | {title}")

            elif receipt.status == 1 and wcol_gained == 0:
                # WCOL dio $0 — NO marcar como success, intentar USDC.e después
                # Puede ser: (a) mercado no-neg_risk, o (b) pérdida real
                print(f"  WCOL $0 (intentando USDC.e...) | {title}")
                # NO poner success = True aquí — deja que Method 2 intente
            else:
                print(f"  WCOL redeem fallo (status={receipt.status}) | {title}")

        except Exception as e:
            print(f"  WCOL error: {str(e)[:60]}")

        # METODO 2: CTF directo con USDC.e (para mercados no-neg_risk)
        if not success:
            try:
                time.sleep(3)
                nonce = w3.eth.get_transaction_count(eoa, 'pending')
                txn = ctf.functions.redeemPositions(
                    w3.to_checksum_address(USDC_E_ADDRESS),
                    bytes.fromhex("00" * 32),
                    cid_bytes,
                    [1, 2]
                ).build_transaction({
                    'from': eoa, 'nonce': nonce, 'gas': 500000,
                    'gasPrice': int(w3.eth.gas_price * 1.2), 'chainId': 137
                })
                signed = w3.eth.account.sign_transaction(txn, pk)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                time.sleep(2)

                usdc_post = usdc.functions.balanceOf(eoa).call() / 1e6
                diff = round(usdc_post - usdc_pre, 2)
                if diff >= 0.01:
                    success = True
                    print(f"  +${diff:.2f} WIN (USDC directo) | {title}")
                elif receipt.status == 1:
                    success = True
                    print(f"  $0.00 LOSS (USDC directo)     | {title}")
            except Exception as e:
                print(f"  USDC error: {str(e)[:60]}")

        if success:
            redeemed += 1
        else:
            # Redeem falló — NO vender como fallback.
            # Antes aquí se vendía la posición en el mercado, pero eso
            # causaba ventas prematuras de posiciones ACTIVAS (partidos
            # en progreso que el oráculo no ha reportado aún).
            # Mejor esperar al próximo ciclo de auto-redeem.
            print(f"  ⏳ Redeem falló, esperando próximo ciclo | {title}")

    # Unwrap cualquier WCOL restante
    time.sleep(5)
    wcol_remaining = wcol.functions.balanceOf(eoa).call()
    if wcol_remaining > 0:
        print(f"\n  Unwrapping {wcol_remaining/1e6:.2f} WCOL restante...")
        try:
            nonce = w3.eth.get_transaction_count(eoa, 'pending')
            txn = wcol.functions.unwrap(eoa, wcol_remaining).build_transaction({
                'from': eoa, 'nonce': nonce, 'gas': 200000,
                'gasPrice': int(w3.eth.gas_price * 1.2), 'chainId': 137
            })
            signed = w3.eth.account.sign_transaction(txn, pk)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status == 1:
                print(f"  Unwrap OK!")
        except Exception as e:
            print(f"  Unwrap error: {str(e)[:60]}")

    time.sleep(3)
    bal_end = usdc.functions.balanceOf(eoa).call() / 1e6

    print(f"\n{'='*60}")
    print(f"  RESULTADO")
    print(f"{'='*60}")
    print(f"  Balance ANTES:   ${bal_start:.2f}")
    print(f"  Balance DESPUES: ${bal_end:.2f}")
    print(f"  Diferencia:      ${bal_end - bal_start:+.2f}")
    print(f"  Cobradas: {redeemed}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(redeem_all())
