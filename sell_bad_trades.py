"""
PolyBot - VENDER TRADES MALOS (Día 3 bugfix)
==============================================
Vende las 2 posiciones que se abrieron por bugs:
1. Weather: "Dallas temperature" (matcheó mal, compró a precio $0)
2. Stock: "BNB Up or Down" (matcheó crypto como bolsa)

USO: python sell_bad_trades.py
"""

import os
import json
import asyncio
import aiohttp
from dotenv import load_dotenv

load_dotenv()

DATA_API_URL = "https://data-api.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Las posiciones malas que queremos vender
BAD_TRADES = [
    "dallas",           # Weather: bug de ciudad
    "bnb",              # Stock: bug crypto como bolsa
    "los angeles",      # Weather: bug between vs above
    "temperature in la",# Variante de LA
]


def get_wallet_address():
    from web3 import Web3
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not pk:
        return None, None
    w3 = Web3()
    account = w3.eth.account.from_key(pk)
    return account.address, pk


def get_clob_client():
    from py_clob_client.client import ClobClient
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not pk:
        return None
    pk_clean = pk[2:] if pk.startswith("0x") else pk

    client = ClobClient(
        host="https://clob.polymarket.com",
        key=pk_clean, chain_id=137, signature_type=0
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


async def find_bad_positions(address: str):
    """Busca solo las posiciones malas."""
    bad_positions = []

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        addresses = [addr for addr in [address.lower(), address, funder.lower() if funder else "", funder] if addr]

        for addr in addresses:
            for endpoint in [
                f"{DATA_API_URL}/positions?user={addr}",
            ]:
                try:
                    async with session.get(endpoint) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data and isinstance(data, list):
                                for pos in data:
                                    title = (pos.get("title") or pos.get("question") or
                                             pos.get("eventTitle") or pos.get("market") or "").lower()
                                    
                                    # Buscar las posiciones malas
                                    is_bad = any(bad_kw in title for bad_kw in BAD_TRADES)
                                    
                                    if is_bad:
                                        token_id = str(pos.get("asset") or pos.get("assetId") or 
                                                      pos.get("token_id") or "")
                                        shares = float(pos.get("size") or pos.get("shares") or 0)
                                        price = float(pos.get("curPrice") or pos.get("price") or 0)
                                        value = float(pos.get("currentValue") or 0)
                                        if value == 0 and shares > 0 and price > 0:
                                            value = shares * price
                                        side = pos.get("outcome") or pos.get("side") or "?"
                                        
                                        if shares > 0 and token_id:
                                            bad_positions.append({
                                                "title": title,
                                                "token_id": token_id,
                                                "shares": shares,
                                                "price": price,
                                                "value": value,
                                                "side": side,
                                                "raw": pos,
                                            })
                                
                                if bad_positions:
                                    return bad_positions
                except Exception as e:
                    print(f"   Error buscando: {e}")
                    continue

    return bad_positions


async def sell_position(client, pos):
    """Vende una posición específica."""
    from py_clob_client.clob_types import OrderArgs, MarketOrderArgs, OrderType
    from py_clob_client.order_builder.constants import SELL

    token_id = pos["token_id"]
    shares = pos["shares"]
    price = pos["price"]

    print(f"      Token: {token_id[:25]}...")
    print(f"      Shares: {shares:.2f} | Precio actual: ${price:.3f}")

    # Intento 1: GTC limit sell (1¢ menos para llenar rápido)
    sell_price = round(max(0.01, price - 0.01), 2)
    print(f"      → GTC sell @ ${sell_price:.2f} x {shares:.2f}...")

    try:
        lo = OrderArgs(
            token_id=token_id,
            price=sell_price,
            size=round(shares, 2),
            side=SELL
        )
        signed = client.create_order(lo)
        resp = client.post_order(signed, OrderType.GTC)

        if resp and isinstance(resp, dict):
            oid = resp.get("orderID", "")
            if oid or resp.get("success"):
                print(f"      ✅ GTC vendido: {oid[:25]}...")
                return True
            print(f"      GTC respuesta: {str(resp)[:100]}")
    except Exception as e:
        print(f"      GTC falló: {str(e)[:80]}")

    # Intento 2: Market sell FOK
    print(f"      → FOK market sell...")
    try:
        sell_amount = round(max(0.50, shares * sell_price), 2)
        mo = MarketOrderArgs(
            token_id=token_id,
            amount=sell_amount,
            side=SELL
        )
        signed_mo = client.create_market_order(mo)
        resp_mo = client.post_order(signed_mo, OrderType.FOK)

        if resp_mo and isinstance(resp_mo, dict):
            oid = resp_mo.get("orderID", "")
            if oid or resp_mo.get("success"):
                print(f"      ✅ FOK vendido: {oid[:25]}...")
                return True
            print(f"      FOK respuesta: {str(resp_mo)[:100]}")
    except Exception as e:
        print(f"      FOK falló: {str(e)[:80]}")

    # Intento 3: GTC con descuento grande (para garantizar fill)
    desperate_price = round(max(0.01, price * 0.80), 2)
    print(f"      → GTC descuento @ ${desperate_price:.2f}...")
    try:
        lo2 = OrderArgs(
            token_id=token_id,
            price=desperate_price,
            size=round(shares, 2),
            side=SELL
        )
        signed2 = client.create_order(lo2)
        resp2 = client.post_order(signed2, OrderType.GTC)

        if resp2 and isinstance(resp2, dict):
            oid = resp2.get("orderID", "")
            if oid or resp2.get("success"):
                print(f"      ✅ GTC descuento vendido: {oid[:25]}...")
                return True
    except Exception as e:
        print(f"      Descuento falló: {str(e)[:80]}")

    return False


async def main():
    print("\n" + "=" * 60)
    print("🔧 PolyBot - VENDER TRADES MALOS (bugfix Día 3)")
    print("=" * 60)
    print()
    print("Buscando las 2 posiciones malas:")
    print("  1. Dallas temperature (weather bug)")
    print("  2. BNB Up or Down (stock bug)")
    print()

    address, pk = get_wallet_address()
    if not address:
        print("❌ Clave privada no configurada en .env")
        return

    print(f"Wallet: {address}")
    print()

    # Buscar posiciones malas
    print("🔍 Buscando posiciones...")
    positions = await find_bad_positions(address)

    if not positions:
        print()
        print("No se encontraron las posiciones malas.")
        print("Posibilidades:")
        print("  - Ya se vendieron/resolvieron")
        print("  - Están bajo un token_id diferente")
        print("  - Usar sell_all.py para ver TODAS las posiciones")
        print()
        print("ALTERNATIVA MANUAL:")
        print("  1. Ve a polymarket.com → tu perfil → Posiciones")
        print("  2. Busca 'Dallas temperature' y 'BNB Up or Down'")
        print("  3. Haz click en cada una → Sell")
        return

    print(f"\n📋 Encontradas {len(positions)} posiciones malas:")
    print("-" * 60)

    total_value = 0
    for i, pos in enumerate(positions, 1):
        title = pos["title"][:55]
        print(f"  {i}. {title}")
        print(f"     {pos['side']} | {pos['shares']:.2f} shares @ ${pos['price']:.3f} | Valor: ${pos['value']:.2f}")
        total_value += pos["value"]

    print(f"\n  💰 Valor total: ${total_value:.2f}")

    # Confirmar
    confirm = input(f"\n¿Vender estas {len(positions)} posiciones? (si/no): ")
    if confirm.strip().lower() not in ("si", "sí", "s", "yes", "y"):
        print("Cancelado.")
        return

    # Inicializar CLOB
    print("\n🔧 Conectando al CLOB...")
    client = get_clob_client()
    if not client:
        print("❌ Error conectando al CLOB")
        return

    # Vender
    print(f"\n{'='*60}")
    print("💰 VENDIENDO POSICIONES MALAS")
    print(f"{'='*60}")

    sold = 0
    for pos in positions:
        title = pos["title"][:50]
        print(f"\n  🔄 Vendiendo: {title}...")
        
        success = await sell_position(client, pos)
        if success:
            sold += 1
            print(f"      ✅ VENDIDO")
        else:
            print(f"      ❌ No se pudo vender automáticamente")
            print(f"      → Véndelo manual en polymarket.com")

    print(f"\n{'='*60}")
    print(f"📊 Resultado: {sold}/{len(positions)} vendidas")
    if sold > 0:
        print(f"Verifica en polymarket.com que se cerraron.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
