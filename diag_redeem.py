"""
PolyBot - Diagnostico de Redeem
=================================
Verifica exactamente donde estan los tokens
y por que no se estan cobrando.
"""

import os, sys, json, asyncio, aiohttp
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
DATA_API_URL = "https://data-api.polymarket.com"

CTF_ABI = [
    {"inputs":[{"name":"account","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"conditionId","type":"bytes32"}],"name":"payoutDenominator","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"conditionId","type":"bytes32"}],"name":"payoutNumerators","outputs":[{"name":"","type":"uint256[]"}],"type":"function"},
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

    if not w3:
        print("No se pudo conectar")
        return

    eoa = w3.eth.account.from_key(pk).address
    ctf = w3.eth.contract(address=w3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E_ADDRESS), abi=ERC20_ABI)

    print(f"\n{'='*60}")
    print(f"  DIAGNOSTICO DE REDEEM")
    print(f"{'='*60}")
    print(f"\n  EOA:    {eoa}")
    print(f"  Funder: {funder}")

    # Balance USDC en ambas direcciones
    eoa_bal = usdc.functions.balanceOf(eoa).call() / 1e6
    print(f"\n  Balance USDC EOA:    ${eoa_bal:.2f}")

    if funder:
        try:
            funder_bal = usdc.functions.balanceOf(w3.to_checksum_address(funder)).call() / 1e6
            print(f"  Balance USDC Funder: ${funder_bal:.2f}")
        except Exception as e:
            print(f"  Balance USDC Funder: Error - {e}")

    # Obtener posiciones
    print(f"\n  Buscando posiciones resueltas...\n")

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
                            print(f"  Posiciones desde: {addr[:10]}... ({len(data)} total)")
                            break
            except:
                continue

    resolved_count = 0
    for pos in positions:
        title = pos.get("title") or pos.get("question") or "?"
        condition_id = pos.get("conditionId") or ""
        asset = pos.get("asset") or ""
        size = float(pos.get("size") or 0)
        cur_price = float(pos.get("curPrice") or 0)

        if not condition_id or size <= 0:
            continue

        try:
            cid_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
            payout_denom = ctf.functions.payoutDenominator(cid_bytes).call()

            if payout_denom == 0:
                continue  # No resuelto

            resolved_count += 1

            # Verificar tokens en AMBAS direcciones
            token_eoa = 0
            token_funder = 0

            if asset:
                asset_id = int(asset)
                try:
                    token_eoa = ctf.functions.balanceOf(eoa, asset_id).call()
                except:
                    pass
                if funder:
                    try:
                        token_funder = ctf.functions.balanceOf(
                            w3.to_checksum_address(funder), asset_id
                        ).call()
                    except:
                        pass

            # Payout info
            try:
                numerators = ctf.functions.payoutNumerators(cid_bytes).call()
                payout_info = f"Payouts: {numerators}"
            except:
                payout_info = f"Denom: {payout_denom}"

            print(f"  {str(title)[:45]}")
            print(f"    Precio: {cur_price:.0%} | {payout_info}")
            print(f"    Tokens EOA:    {token_eoa / 1e6:.2f}")
            print(f"    Tokens Funder: {token_funder / 1e6:.2f}")

            if token_eoa > 0:
                print(f"    >> COBRABLE desde EOA")
            elif token_funder > 0:
                print(f"    >> COBRABLE desde FUNDER (necesita proxy)")
            else:
                print(f"    >> Ya cobrado (0 tokens)")
            print()

        except Exception as e:
            pass

    print(f"  Total resueltas: {resolved_count}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(main())
