"""
PolyBot - Deep Diagnostic Redeem
==================================
Investiga EXACTAMENTE por que el redeem no paga.
Muestra toda la info on-chain de cada posicion.
"""

import os, sys, json, asyncio, aiohttp
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
DATA_API_URL = "https://data-api.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"

CTF_ABI = [
    {"inputs":[{"name":"account","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"conditionId","type":"bytes32"}],"name":"payoutDenominator","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"conditionId","type":"bytes32"}],"name":"payoutNumerators","outputs":[{"name":"","type":"uint256[]"}],"type":"function"},
    {"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"type":"function"},
]
ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
]

async def main():
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")

    w3 = None
    for rpc in ["https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic"]:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
            if w3.is_connected():
                break
        except:
            continue

    eoa = w3.eth.account.from_key(pk).address
    ctf = w3.eth.contract(address=w3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E_ADDRESS), abi=ERC20_ABI)

    print(f"\n{'='*60}")
    print(f"  DEEP DIAGNOSTIC")
    print(f"{'='*60}")
    print(f"  EOA: {eoa}")
    print(f"  Funder: {funder}")
    print(f"  USDC EOA: ${usdc.functions.balanceOf(eoa).call() / 1e6:.2f}")

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

    # Solo posiciones resueltas
    for pos in positions:
        title = pos.get("title") or pos.get("question") or "?"
        condition_id = pos.get("conditionId") or ""
        asset = pos.get("asset") or ""
        size = float(pos.get("size") or 0)
        cur_price = float(pos.get("curPrice") or 0)
        side = pos.get("outcome") or "?"
        neg_risk = pos.get("negRisk", False)
        market_slug = pos.get("marketSlug") or pos.get("slug") or ""

        if not condition_id or size <= 0:
            continue

        try:
            cid_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
            payout_denom = ctf.functions.payoutDenominator(cid_bytes).call()

            if payout_denom == 0:
                continue

            print(f"\n  {'='*55}")
            print(f"  {str(title)[:55]}")
            print(f"  Side: {side} | Precio: {cur_price:.0%}")
            print(f"  ConditionId: {condition_id[:20]}...")
            print(f"  Asset (token_id): {asset[:20]}..." if asset else "  Asset: N/A")
            print(f"  NegRisk (API): {neg_risk}")
            print(f"  Slug: {market_slug}")

            # Payout info
            print(f"  PayoutDenominator: {payout_denom}")
            try:
                numerators = ctf.functions.payoutNumerators(cid_bytes).call()
                print(f"  PayoutNumerators: {numerators}")
                # Determinar cual outcome gano
                for i, n in enumerate(numerators):
                    status = "GANADOR" if n > 0 else "PERDEDOR"
                    print(f"    Outcome {i} (indexSet={1 << i}): payout={n} [{status}]")
            except Exception as e:
                print(f"  PayoutNumerators: ERROR - {e}")

            # Tokens en ambas direcciones
            if asset:
                asset_id = int(asset)
                tok_eoa = ctf.functions.balanceOf(eoa, asset_id).call() / 1e6
                print(f"  Tokens EOA: {tok_eoa:.2f}")
                if funder:
                    tok_funder = ctf.functions.balanceOf(w3.to_checksum_address(funder), asset_id).call() / 1e6
                    print(f"  Tokens Funder: {tok_funder:.2f}")

                # Determinar a que outcome pertenece nuestro token
                # Token IDs en CTF se generan como: positionId = hash(collateral, collectionId)
                # Necesitamos saber si este token es outcome 0 o outcome 1
                print(f"  Token ID completo: {asset}")

            # Buscar mas info en Gamma
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    async with session.get(f"{GAMMA_API_URL}/markets",
                        params={"conditionId": condition_id, "limit": 1}) as resp:
                        if resp.status == 200:
                            markets = await resp.json()
                            if markets:
                                m = markets[0]
                                print(f"  Gamma negRisk: {m.get('negRisk')}")
                                print(f"  Gamma questionID: {m.get('questionID', 'N/A')[:20]}...")
                                print(f"  Gamma resolved: {m.get('resolved')}")
                                print(f"  Gamma closed: {m.get('closed')}")
                                
                                # Token IDs de ambos outcomes
                                tokens = m.get("clobTokenIds") or ""
                                if tokens:
                                    if isinstance(tokens, str):
                                        try:
                                            tokens = json.loads(tokens)
                                        except:
                                            tokens = []
                                    if tokens:
                                        for i, tid in enumerate(tokens):
                                            match = " <<< NUESTRO" if str(tid) == str(asset) else ""
                                            print(f"  Outcome {i} tokenId: {str(tid)[:20]}...{match}")
                            else:
                                print(f"  Gamma: mercado no encontrado por conditionId")
            except Exception as e:
                print(f"  Gamma error: {e}")

        except Exception as e:
            pass

    print(f"\n{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(main())
