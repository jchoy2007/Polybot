"""
PolyBot - VENDER POSICIONES ABIERTAS (v2)
===========================================
Encuentra y vende tus posiciones en Polymarket.

Polymarket usa proxy wallets, así que este script:
1. Busca tu dirección proxy vía la Data API
2. Encuentra tus posiciones reales
3. Vende cada una

USO: python sell_all.py
"""

import os
import json
import asyncio
import aiohttp
from dotenv import load_dotenv

load_dotenv()

GAMMA_API_URL = "https://gamma-api.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

CTF_ABI = [{
    "inputs": [
        {"name": "account", "type": "address"},
        {"name": "id", "type": "uint256"}
    ],
    "name": "balanceOf",
    "outputs": [{"name": "", "type": "uint256"}],
    "type": "function"
}]


def get_wallet_address():
    """Obtiene la dirección de la wallet."""
    from web3 import Web3
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not pk:
        return None, None
    w3 = Web3()
    account = w3.eth.account.from_key(pk)
    return account.address, pk


def get_clob_client():
    """
    Inicializa el CLOB client probando múltiples configuraciones.
    Polymarket usa proxy wallets, así que necesitamos la config correcta.
    """
    from py_clob_client.client import ClobClient
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not pk:
        return None
    pk_clean = pk[2:] if pk.startswith("0x") else pk
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")

    # Intentar usar las API creds guardadas en .env
    api_key = os.getenv("POLYMARKET_API_KEY", "")
    api_secret = os.getenv("POLYMARKET_SECRET", "")
    api_passphrase = os.getenv("POLYMARKET_PASSPHRASE", "")

    configs = []

    # Config 1: EOA directo sin funder (FUNCIONA para comprar, debería funcionar para vender)
    configs.append({
        "name": "EOA directo",
        "sig_type": 0, "funder": None, "use_stored_creds": False
    })

    # Config 2: EOA + funder + derive creds
    if funder:
        configs.append({
            "name": "EOA + funder + derive",
            "sig_type": 0, "funder": funder, "use_stored_creds": False
        })

    # Config 3: Browser proxy (sig_type=2)
    if funder:
        configs.append({
            "name": "Browser proxy + funder",
            "sig_type": 2, "funder": funder, "use_stored_creds": False
        })

    # Config 4: signature_type=1 + funder
    if funder:
        configs.append({
            "name": "Magic + funder + derive",
            "sig_type": 1, "funder": funder, "use_stored_creds": False
        })

    # Config 5: con stored creds
    if funder and api_key:
        configs.append({
            "name": "EOA + funder + stored creds",
            "sig_type": 0, "funder": funder, "use_stored_creds": True
        })

    for cfg in configs:
        try:
            print(f"   Probando config: {cfg['name']}...")

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

            if cfg["use_stored_creds"] and api_key and api_secret and api_passphrase:
                from py_clob_client.clob_types import ApiCreds
                creds = ApiCreds(
                    api_key=api_key,
                    api_secret=api_secret,
                    api_passphrase=api_passphrase
                )
                client.set_api_creds(creds)
                print(f"   → Usando API creds de .env")
            else:
                creds = client.create_or_derive_api_creds()
                client.set_api_creds(creds)
                print(f"   → API creds derivadas")

            # Test: intentar obtener órdenes abiertas para verificar que funciona
            try:
                orders = client.get_orders()
                print(f"   ✅ Config funciona! (orders response OK)")
                return client
            except Exception as test_e:
                err = str(test_e)[:60]
                if "invalid" in err.lower() or "401" in err or "403" in err:
                    print(f"   ❌ Config falló: {err}")
                    continue
                else:
                    # Otro error (puede ser OK, solo no hay órdenes)
                    print(f"   ⚠️ Test parcial: {err} (intentando de todas formas)")
                    return client

        except Exception as e:
            print(f"   ❌ Error: {str(e)[:60]}")
            continue

    print("   ❌ Ninguna configuración funcionó")
    return None


async def find_positions_data_api(address: str):
    """Intenta buscar posiciones vía Polymarket Data API."""
    positions = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        # Intentar múltiples endpoints y direcciones
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        addresses = [address.lower(), address]
        if funder:
            addresses.extend([funder.lower(), funder])

        for addr in addresses:
            for base_url in [DATA_API_URL, GAMMA_API_URL]:
                for endpoint in [
                    f"{base_url}/positions?user={addr}",
                    f"{base_url}/currentActivity?user={addr}",
                ]:
                    try:
                        async with session.get(endpoint) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data and isinstance(data, list) and len(data) > 0:
                                    print(f"   ✅ Encontradas {len(data)} posiciones vía API")

                                    # Depurar: mostrar campos del primer registro
                                    first = data[0]
                                    print(f"   Campos disponibles: {list(first.keys())[:10]}")

                                    # Normalizar cada posición
                                    for item in data:
                                        pos = normalize_position(item)
                                        if pos:
                                            positions.append(pos)

                                    if positions:
                                        return positions
                    except:
                        continue

    return positions


def normalize_position(raw: dict) -> dict:
    """Convierte datos de la API a formato estándar."""
    # Intentar múltiples nombres de campo
    title = (raw.get("title") or raw.get("question") or
             raw.get("eventTitle") or raw.get("market") or
             raw.get("slug") or raw.get("groupItemTitle") or
             raw.get("market_slug") or "Mercado desconocido")

    # Token ID
    token_id = (raw.get("asset") or raw.get("assetId") or
                raw.get("token_id") or raw.get("tokenId") or
                raw.get("clobTokenId") or "")

    # Side/Outcome
    side = (raw.get("outcome") or raw.get("side") or
            raw.get("direction") or raw.get("outcomeIndex") or "?")

    # Shares/Size
    shares = 0
    for key in ["size", "shares", "quantity", "amount", "rawSize"]:
        val = raw.get(key)
        if val is not None:
            try:
                shares = float(val)
                if shares > 0:
                    break
            except:
                continue

    # Price
    price = 0
    for key in ["curPrice", "price", "currentPrice", "avgPrice", "averageCost"]:
        val = raw.get(key)
        if val is not None:
            try:
                price = float(val)
                if price > 0:
                    break
            except:
                continue

    # Value
    value = 0
    for key in ["currentValue", "value", "marketValue"]:
        val = raw.get(key)
        if val is not None:
            try:
                value = float(val)
                if value > 0:
                    break
            except:
                continue

    if value == 0 and shares > 0 and price > 0:
        value = shares * price

    if not token_id and shares == 0:
        return None

    return {
        "title": str(title)[:60],
        "token_id": str(token_id),
        "side": str(side),
        "shares": shares,
        "price": price,
        "value": value,
        "raw": raw  # Guardar datos crudos por si acaso
    }


async def find_positions_onchain(address: str):
    """Busca posiciones directamente en el contrato CTF."""
    from web3 import Web3

    positions = []
    rpcs = [
        "https://polygon-bor-rpc.publicnode.com",
        "https://1rpc.io/matic",
        "https://polygon-rpc.com",
        "https://polygon.llamarpc.com",
    ]

    w3 = None
    for rpc in rpcs:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
            if w3.is_connected():
                break
        except:
            continue

    if not w3 or not w3.is_connected():
        print("   ❌ No se pudo conectar a Polygon")
        return positions

    ctf = w3.eth.contract(
        address=w3.to_checksum_address(CTF_ADDRESS),
        abi=CTF_ABI
    )

    # Buscar en mercados activos crypto de April 2
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(
            f"{GAMMA_API_URL}/markets",
            params={"active": "true", "closed": "false",
                    "limit": 100, "order": "endDate", "ascending": "true"}
        ) as resp:
            if resp.status != 200:
                return positions

            markets = await resp.json()

            # También buscar mercados cerrados recientes
            async with session.get(
                f"{GAMMA_API_URL}/markets",
                params={"closed": "true", "limit": 50,
                        "order": "endDate", "ascending": "false"}
            ) as resp2:
                if resp2.status == 200:
                    closed = await resp2.json()
                    markets.extend(closed)

            # Probar con la dirección directa Y posibles proxies
            addresses_to_check = [address]

            # Intentar derivar funder address
            funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
            if funder and funder != address:
                addresses_to_check.append(funder)

            for check_addr in addresses_to_check:
                print(f"   Revisando dirección: {check_addr[:12]}...")

                for m in markets:
                    tokens_str = m.get("clobTokenIds", "[]")
                    if isinstance(tokens_str, str):
                        try:
                            tokens = json.loads(tokens_str)
                        except:
                            continue
                    else:
                        tokens = tokens_str

                    if not tokens or len(tokens) < 2:
                        continue

                    for i, token_id in enumerate(tokens):
                        try:
                            token_int = int(token_id)
                            balance = ctf.functions.balanceOf(
                                w3.to_checksum_address(check_addr),
                                token_int
                            ).call()

                            if balance > 0:
                                outcomes = m.get("outcomePrices", "[]")
                                if isinstance(outcomes, str):
                                    prices = json.loads(outcomes)
                                else:
                                    prices = outcomes

                                side = "UP/YES" if i == 0 else "DOWN/NO"
                                price = float(prices[i]) if i < len(prices) else 0.5
                                shares = balance / 1e6
                                value = shares * price

                                positions.append({
                                    "question": m.get("question", ""),
                                    "market_id": m.get("id", ""),
                                    "token_id": token_id,
                                    "token_index": i,
                                    "side": side,
                                    "shares": shares,
                                    "price": price,
                                    "value": value,
                                    "balance_raw": balance,
                                    "owner_address": check_addr
                                })
                        except:
                            continue

                if positions:
                    break  # Found positions, stop checking addresses

    return positions


async def sell_position(client, pos: dict):
    """Vende una posición específica con manejo completo de errores."""
    token_id = pos.get("token_id", "")
    shares = pos.get("shares", 0)
    price = pos.get("price", 0.5)
    raw = pos.get("raw", {})

    if not token_id or shares <= 0:
        return False, "Sin token_id o shares", "SKIP"

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType, OrderArgs
        from py_clob_client.order_builder.constants import SELL

        # Obtener tick size y negRisk del mercado
        neg_risk = raw.get("negRisk", False)
        # Default tick size
        tick_size = "0.01"

        # Intentar obtener info del mercado para tick_size
        try:
            market_info = client.get_market(token_id)
            if market_info and isinstance(market_info, dict):
                tick_size = str(market_info.get("minimum_tick_size", "0.01"))
                neg_risk = market_info.get("neg_risk", neg_risk)
        except:
            pass

        print(f"      Token: {token_id[:20]}... | Shares: {shares:.1f} | Price: ${price:.3f}")
        print(f"      TickSize: {tick_size} | NegRisk: {neg_risk}")

        # === INTENTO 1: Limit order GTC (más confiable para venta) ===
        sell_price = round(price - 0.01, 3)  # Un centavo menos para llenar rápido
        sell_price = max(0.01, sell_price)

        # Redondear al tick size
        ts = float(tick_size)
        if ts > 0:
            sell_price = round(round(sell_price / ts) * ts, 4)

        print(f"      Intentando GTC @ ${sell_price:.4f} x {shares:.1f} shares...")

        try:
            lo = OrderArgs(
                token_id=token_id,
                price=sell_price,
                size=round(shares, 2),
                side=SELL
            )
            signed = client.create_order(lo)
            resp = client.post_order(signed, OrderType.GTC)

            print(f"      Respuesta GTC: {str(resp)[:120]}")

            if resp and isinstance(resp, dict):
                oid = resp.get("orderID", resp.get("id", ""))
                if oid or resp.get("success"):
                    return True, oid, "GTC"
                # Mostrar error completo
                err = resp.get("error", resp.get("error_message", ""))
                if err:
                    print(f"      Error GTC: {str(err)[:100]}")
        except Exception as e:
            print(f"      Error GTC: {str(e)[:100]}")

        # === INTENTO 2: Market order FOK ===
        print(f"      Intentando FOK market sell...")
        try:
            # Para sell, amount = valor a recibir
            sell_amount = round(shares * sell_price, 2)
            sell_amount = max(1.0, sell_amount)

            mo = MarketOrderArgs(
                token_id=token_id,
                amount=sell_amount,
                side=SELL
            )
            signed_mo = client.create_market_order(mo)
            resp_mo = client.post_order(signed_mo, OrderType.FOK)

            print(f"      Respuesta FOK: {str(resp_mo)[:120]}")

            if resp_mo and isinstance(resp_mo, dict):
                oid = resp_mo.get("orderID", resp_mo.get("id", ""))
                if oid or resp_mo.get("success"):
                    return True, oid, "FOK"
                err = resp_mo.get("error", resp_mo.get("error_message", ""))
                if err:
                    print(f"      Error FOK: {str(err)[:100]}")
        except Exception as e:
            print(f"      Error FOK: {str(e)[:100]}")

        # === INTENTO 3: Limit order mucho más barato (para garantizar fill) ===
        desperate_price = round(price * 0.85, 3)  # 15% descuento
        desperate_price = max(0.01, desperate_price)
        if ts > 0:
            desperate_price = round(round(desperate_price / ts) * ts, 4)

        print(f"      Último intento GTC @ ${desperate_price:.4f} (descuento)...")
        try:
            lo2 = OrderArgs(
                token_id=token_id,
                price=desperate_price,
                size=round(shares, 2),
                side=SELL
            )
            signed2 = client.create_order(lo2)
            resp2 = client.post_order(signed2, OrderType.GTC)

            print(f"      Respuesta: {str(resp2)[:120]}")

            if resp2 and isinstance(resp2, dict):
                oid = resp2.get("orderID", resp2.get("id", ""))
                if oid or resp2.get("success"):
                    return True, oid, "GTC_DISCOUNT"
        except Exception as e:
            print(f"      Error: {str(e)[:100]}")

        return False, "Todos los intentos fallaron", "FAILED"

    except Exception as e:
        return False, str(e)[:100], "ERROR"


async def main():
    print("\n🤖 PolyBot - VENDER POSICIONES ABIERTAS")
    print("=" * 60)

    address, pk = get_wallet_address()
    if not address:
        print("❌ Clave privada no configurada en .env")
        return

    print(f"   Wallet EOA: {address}")
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    if funder:
        print(f"   Wallet Proxy: {funder}")

    # Paso 1: Buscar posiciones
    print("\n🔍 Buscando posiciones...")

    # Intentar Data API primero
    positions = await find_positions_data_api(address)

    # Si no encontró, buscar on-chain
    if not positions:
        print("   Data API sin resultados, buscando on-chain...")
        positions = await find_positions_onchain(address)

    if not positions:
        print("\n   ❌ No se encontraron posiciones.")
        print("\n   Posibles razones:")
        print("   1. Polymarket usa una dirección PROXY diferente a tu wallet")
        print("   2. Necesitas agregar POLYMARKET_FUNDER_ADDRESS en tu .env")
        print("")
        print("   CÓMO ENCONTRAR TU DIRECCIÓN PROXY:")
        print("   1. Ve a polymarket.com → tu perfil (ícono arriba derecha)")
        print("   2. Haz clic en tu nombre/dirección")
        print("   3. Copia la dirección que aparece (empieza con 0x...)")
        print("   4. Agrégala en .env como:")
        print("      POLYMARKET_FUNDER_ADDRESS=0x...tu_dirección_proxy...")
        print("   5. Corre este script de nuevo")
        print("")
        print("   ALTERNATIVA: Vende manualmente en polymarket.com:")
        print("   Ve a tu perfil → Posiciones → click en cada una → Sell")
        return

    # Mostrar posiciones
    print(f"\n📋 {len(positions)} POSICIONES ENCONTRADAS:")
    print("-" * 60)
    total_value = 0
    for i, pos in enumerate(positions, 1):
        title = pos.get('title') or pos.get('question', 'Mercado')
        side = pos.get('side', '?')
        shares = pos.get('shares', 0)
        price = pos.get('price', 0)
        value = pos.get('value', 0)
        token_id = pos.get('token_id', '')

        print(
            f"   {i}. {str(title)[:55]}\n"
            f"      {side} | {shares:.1f} acciones @ "
            f"${price:.3f} | Valor: ${value:.2f}"
            f"{' | Token: ' + token_id[:15] + '...' if token_id else ''}"
        )
        total_value += value

    print(f"\n   💰 Valor total estimado: ${total_value:.2f}")

    # Confirmar
    confirm = input(f"\n¿Vender todo por ~${total_value:.2f}? (si/no): ")
    if confirm.lower() not in ("si", "sí", "s", "yes", "y"):
        print("Cancelado.")
        return

    # Inicializar CLOB client
    client = get_clob_client()
    if not client:
        print("❌ Error inicializando CLOB client")
        return

    # Vender cada posición
    print(f"\n{'=' * 60}")
    print(f"💰 VENDIENDO {len(positions)} POSICIONES")
    print(f"{'=' * 60}")

    sold = 0
    recovered = 0.0

    for pos in positions:
        title = pos.get("title") or pos.get("question", "Mercado")
        q = str(title)[:50]
        token_id = pos.get("token_id", "")
        shares = pos.get("shares", 0)
        price = pos.get("price", 0)
        value = pos.get("value", 0)

        if not token_id or shares <= 0:
            print(f"\n   ⏭️ {q}... (sin token_id o shares, saltando)")
            continue

        print(f"\n   🔄 {q}...")

        success, order_id, method = await sell_position(client, pos)

        if success:
            sold += 1
            recovered += value
            print(f"      ✅ Vendido ({method}) | ~${value:.2f}")
        else:
            print(f"      ⚠️ No se pudo vender: {order_id}")

    print(f"\n{'=' * 60}")
    print(f"📊 RESULTADO")
    print(f"{'=' * 60}")
    print(f"   Vendidas: {sold}/{len(positions)}")
    print(f"   Recuperado estimado: ~${recovered:.2f}")
    if sold > 0:
        print(f"\n   Verifica en polymarket.com que las posiciones se cerraron.")

    # Preguntar si limpiar historial de duplicados
    clean = input(f"\n¿Limpiar historial de duplicados para empezar fresco? (si/no): ")
    if clean.lower() in ("si", "sí", "s", "yes", "y"):
        import shutil
        from datetime import datetime as dt
        timestamp = dt.now().strftime("%Y%m%d_%H%M")

        # Backup y limpiar bets_placed.json
        try:
            if os.path.exists("data/bets_placed.json"):
                shutil.copy("data/bets_placed.json", f"data/bets_placed_backup_{timestamp}.json")
                with open("data/bets_placed.json", "w") as f:
                    json.dump({"market_ids": [], "history": []}, f, indent=2)
                print(f"   ✅ bets_placed.json limpiado (backup guardado)")
        except Exception as e:
            print(f"   ❌ Error limpiando bets_placed: {e}")

        # Backup y limpiar trade_results.json
        try:
            if os.path.exists("data/trade_results.json"):
                shutil.copy("data/trade_results.json", f"data/trade_results_backup_{timestamp}.json")
                with open("data/trade_results.json", "w") as f:
                    json.dump([], f, indent=2)
                print(f"   ✅ trade_results.json limpiado (backup guardado)")
        except Exception as e:
            print(f"   ❌ Error limpiando trade_results: {e}")

        print(f"\n   🆕 Bot listo para empezar fresco!")
        print(f"   Los backups están en data/bets_placed_backup_{timestamp}.json")
        print(f"   y data/trade_results_backup_{timestamp}.json")


if __name__ == "__main__":
    asyncio.run(main())
