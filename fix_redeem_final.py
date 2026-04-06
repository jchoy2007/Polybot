"""
PolyBot - Fix Redeem FINAL
============================
Consulta CLOB API para obtener neg_risk real y conditionId correcto.
Usa el metodo adecuado para cada tipo de mercado.
"""

import os, sys, json, asyncio, time, aiohttp
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
DATA_API_URL = "https://data-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"

CTF_ABI = [
    {"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"type":"function"},
    {"inputs":[{"name":"account","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"conditionId","type":"bytes32"}],"name":"payoutDenominator","outputs":[{"name":"","type":"uint256"}],"type":"function"},
]
NEG_RISK_ABI = [
    {"inputs":[{"name":"conditionId","type":"bytes32"},{"name":"amounts","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"type":"function"},
]
ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
]

def connect_polygon():
    for rpc in ["https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic",
                "https://polygon-rpc.com"]:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
            if w3.is_connected():
                return w3
        except:
            continue
    return None

async def get_clob_neg_risk(session, token_id):
    """Consulta CLOB API para obtener info real de neg_risk."""
    try:
        async with session.get(f"{CLOB_API_URL}/neg-risk?token_id={token_id}") as resp:
            if resp.status == 200:
                data = await resp.json()
                return data  # {"neg_risk": true/false}
    except:
        pass
    return {"neg_risk": False}

async def get_clob_market(session, token_id):
    """Consulta CLOB API para obtener info del mercado por token_id."""
    try:
        async with session.get(f"{CLOB_API_URL}/markets?token_id={token_id}") as resp:
            if resp.status == 200:
                data = await resp.json()
                return data
    except:
        pass
    return None

async def main():
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")

    w3 = connect_polygon()
    if not w3:
        print("No se pudo conectar")
        return

    eoa = w3.eth.account.from_key(pk).address
    ctf = w3.eth.contract(address=w3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    neg = w3.eth.contract(address=w3.to_checksum_address(NEG_RISK_ADAPTER), abi=NEG_RISK_ABI)
    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E_ADDRESS), abi=ERC20_ABI)

    bal_start = usdc.functions.balanceOf(eoa).call() / 1e6
    print(f"\n{'='*60}")
    print(f"  Fix Redeem - Investigando CLOB API")
    print(f"  Balance: ${bal_start:.2f}")
    print(f"{'='*60}\n")

    # Obtener posiciones
    positions = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for addr in [funder, eoa]:
            if not addr:
                continue
            try:
                async with session.get(f"{DATA_API_URL}/positions?user={addr.lower()}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data:
                            positions = data
                            break
            except:
                continue

    # Filtrar resueltas con tokens
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for pos in positions:
            title = pos.get("title") or pos.get("question") or "?"
            condition_id = pos.get("conditionId") or ""
            asset = pos.get("asset") or ""
            size = float(pos.get("size") or 0)
            cur_price = float(pos.get("curPrice") or 0)
            side = pos.get("outcome") or "?"

            if not condition_id or not asset or size <= 0:
                continue

            try:
                cid_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
                payout_denom = ctf.functions.payoutDenominator(cid_bytes).call()
                if payout_denom == 0:
                    continue

                token_bal = ctf.functions.balanceOf(eoa, int(asset)).call()
                if token_bal <= 0:
                    continue
            except:
                continue

            tokens_human = token_bal / 1e6
            is_win = cur_price >= 0.50

            print(f"  {str(title)[:50]}")
            print(f"  Side: {side} | {'WIN' if is_win else 'LOSS'} | Tokens: {tokens_human:.2f}")

            # Consultar CLOB API
            neg_risk_info = await get_clob_neg_risk(session, asset)
            market_info = await get_clob_market(session, asset)

            is_neg_risk = neg_risk_info.get("neg_risk", False)
            print(f"  CLOB neg_risk: {is_neg_risk}")

            if market_info:
                real_condition = market_info.get("condition_id", "")
                question_id = market_info.get("question_id", "")
                print(f"  CLOB condition_id: {real_condition[:20]}..." if real_condition else "  CLOB condition_id: N/A")
                print(f"  CLOB question_id:  {question_id[:20]}..." if question_id else "  CLOB question_id: N/A")

                # Si el condition_id del CLOB es diferente al del Data API, ese es el problema!
                if real_condition and real_condition != condition_id:
                    print(f"  *** CONDITION_ID DIFERENTE! Data API vs CLOB ***")
                    print(f"      Data API: {condition_id[:20]}...")
                    print(f"      CLOB:     {real_condition[:20]}...")
            else:
                print(f"  CLOB market: no encontrado")
                question_id = ""
                real_condition = ""

            # INTENTAR REDEEM con la info correcta
            bal_pre = usdc.functions.balanceOf(eoa).call() / 1e6
            success = False

            if is_neg_risk and question_id:
                # Neg risk: usar question_id con NEG_RISK_ADAPTER
                print(f"  Intentando NegRisk con question_id...")
                try:
                    qid_bytes = bytes.fromhex(question_id[2:] if question_id.startswith("0x") else question_id)
                    nonce = w3.eth.get_transaction_count(eoa)
                    txn = neg.functions.redeemPositions(qid_bytes, []).build_transaction({
                        'from': eoa, 'nonce': nonce, 'gas': 500000,
                        'gasPrice': w3.eth.gas_price, 'chainId': 137
                    })
                    signed = w3.eth.account.sign_transaction(txn, pk)
                    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                    time.sleep(3)
                    bal_post = usdc.functions.balanceOf(eoa).call() / 1e6
                    diff = round(bal_post - bal_pre, 2)
                    print(f"  NegRisk+QuestionID: status={receipt.status} diff=${diff:+.2f}")
                    if diff >= 0.01:
                        success = True
                        print(f"  +${diff:.2f} COBRADO!")
                    elif receipt.status == 1 and not is_win:
                        success = True
                        print(f"  $0.00 LOSS cobrada")
                except Exception as e:
                    print(f"  NegRisk+QID error: {str(e)[:60]}")

            if not success and real_condition and real_condition != condition_id:
                # Intentar CTF con el condition_id CORRECTO del CLOB
                print(f"  Intentando CTF con CLOB condition_id...")
                try:
                    real_cid_bytes = bytes.fromhex(real_condition[2:] if real_condition.startswith("0x") else real_condition)
                    nonce = w3.eth.get_transaction_count(eoa)
                    txn = ctf.functions.redeemPositions(
                        w3.to_checksum_address(USDC_E_ADDRESS),
                        bytes.fromhex("00" * 32),
                        real_cid_bytes,
                        [1, 2]
                    ).build_transaction({
                        'from': eoa, 'nonce': nonce, 'gas': 500000,
                        'gasPrice': w3.eth.gas_price, 'chainId': 137
                    })
                    signed = w3.eth.account.sign_transaction(txn, pk)
                    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                    time.sleep(3)
                    bal_post = usdc.functions.balanceOf(eoa).call() / 1e6
                    diff = round(bal_post - bal_pre, 2)
                    print(f"  CTF+CLOB_CID: status={receipt.status} diff=${diff:+.2f}")
                    if diff >= 0.01:
                        success = True
                        print(f"  +${diff:.2f} COBRADO!")
                except Exception as e:
                    print(f"  CTF+CLOB error: {str(e)[:60]}")

            if not success:
                # Intentar con condition_id original y diferentes indexSets
                for idx in [[1], [2], [1, 2]]:
                    try:
                        nonce = w3.eth.get_transaction_count(eoa)
                        txn = ctf.functions.redeemPositions(
                            w3.to_checksum_address(USDC_E_ADDRESS),
                            bytes.fromhex("00" * 32),
                            cid_bytes,
                            idx
                        ).build_transaction({
                            'from': eoa, 'nonce': nonce, 'gas': 500000,
                            'gasPrice': w3.eth.gas_price, 'chainId': 137
                        })
                        signed = w3.eth.account.sign_transaction(txn, pk)
                        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                        time.sleep(2)
                        bal_post = usdc.functions.balanceOf(eoa).call() / 1e6
                        diff = round(bal_post - bal_pre, 2)
                        if diff >= 0.01:
                            success = True
                            print(f"  +${diff:.2f} CTF idx={idx}")
                            break
                    except:
                        continue

            if not success:
                print(f"  >> NO SE PUDO COBRAR")

            print()

    bal_end = usdc.functions.balanceOf(eoa).call() / 1e6
    print(f"  Balance ANTES:   ${bal_start:.2f}")
    print(f"  Balance DESPUES: ${bal_end:.2f}")
    print(f"  Diferencia:      ${bal_end - bal_start:+.2f}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(main())
