"""
PolyBot - Configuración Inicial
=================================
Este script hace 3 cosas:
1. Genera tus API keys de Polymarket (CLOB)
2. Aprueba los contratos necesarios para operar
3. Guarda todo en tu archivo .env

EJECUTAR UNA SOLA VEZ antes de usar el bot.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


def step1_generate_api_keys():
    """Genera API keys para el CLOB de Polymarket."""
    print("\n" + "=" * 50)
    print("PASO 1: Generar API Keys de Polymarket")
    print("=" * 50)

    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")

    if not private_key or private_key == "0x...":
        print("❌ ERROR: No se encontró tu clave privada en .env")
        print("   Abre .env y pega tu clave privada en POLYGON_WALLET_PRIVATE_KEY=0x...")
        return None

    # Quitar 0x si lo tiene para py-clob-client
    pk_clean = private_key
    if pk_clean.startswith("0x"):
        pk_clean = pk_clean[2:]

    try:
        from py_clob_client.client import ClobClient

        host = "https://clob.polymarket.com"
        chain_id = 137  # Polygon

        # Obtener funder address (proxy wallet de Polymarket)
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")

        print("   Conectando a Polymarket CLOB...")

        if funder:
            print(f"   Proxy wallet: {funder[:12]}...")
            # MetaMask con proxy wallet (depositó vía web)
            client = ClobClient(
                host, key=pk_clean, chain_id=chain_id,
                signature_type=0, funder=funder
            )
        else:
            # Sin proxy, directo
            client = ClobClient(host, key=pk_clean, chain_id=chain_id)

        print("   Generando API credentials...")
        api_creds = client.create_or_derive_api_creds()

        print("\n   ✅ API Keys generadas exitosamente!")
        print(f"   API Key:    {api_creds.api_key}")
        print(f"   Secret:     {api_creds.api_secret[:20]}...")
        print(f"   Passphrase: {api_creds.api_passphrase[:20]}...")

        return {
            "api_key": api_creds.api_key,
            "api_secret": api_creds.api_secret,
            "api_passphrase": api_creds.api_passphrase
        }

    except Exception as e:
        print(f"❌ Error generando API keys: {e}")
        print("   Asegúrate de que tu clave privada es correcta")
        return None


def step2_approve_contracts():
    """Aprueba los contratos de Polymarket para poder operar."""
    print("\n" + "=" * 50)
    print("PASO 2: Aprobar Contratos de Polymarket")
    print("=" * 50)

    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not private_key or private_key == "0x...":
        print("❌ ERROR: Clave privada no configurada")
        return False

    try:
        from web3 import Web3

        # Conectar a Polygon
        rpc_url = "https://polygon-bor-rpc.publicnode.com"
        w3 = Web3(Web3.HTTPProvider(rpc_url))

        if not w3.is_connected():
            # Probar RPC alternativo
            rpc_url = "https://rpc-mainnet.matic.quiknode.pro"
            w3 = Web3(Web3.HTTPProvider(rpc_url))

        if not w3.is_connected():
            print("❌ No se pudo conectar a Polygon. Verificando internet...")
            return False

        print(f"   ✅ Conectado a Polygon (Chain ID: {w3.eth.chain_id})")

        # Obtener cuenta
        account = w3.eth.account.from_key(private_key)
        address = account.address
        print(f"   Tu dirección: {address}")

        # Verificar balance de POL (para gas)
        pol_balance = w3.eth.get_balance(address)
        pol_eth = w3.from_wei(pol_balance, 'ether')
        print(f"   Balance POL: {pol_eth:.4f}")

        if pol_eth < 0.01:
            print("   ⚠️ Necesitas al menos 0.01 POL para gas")
            print("   Envía un poco de POL a tu wallet desde Binance")
            return False

        # Dirección USDC en Polygon
        USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        # Polymarket Exchange (CTF Exchange)
        CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
        # Neg Risk Exchange
        NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

        # ABI mínimo para approve
        ERC20_ABI = [
            {
                "inputs": [
                    {"name": "spender", "type": "address"},
                    {"name": "amount", "type": "uint256"}
                ],
                "name": "approve",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function"
            },
            {
                "inputs": [
                    {"name": "owner", "type": "address"},
                    {"name": "spender", "type": "address"}
                ],
                "name": "allowance",
                "outputs": [{"name": "", "type": "uint256"}],
                "type": "function"
            },
            {
                "inputs": [{"name": "account", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}],
                "type": "function"
            }
        ]

        usdc_contract = w3.eth.contract(
            address=w3.to_checksum_address(USDC_ADDRESS),
            abi=ERC20_ABI
        )

        # Verificar balance USDC
        usdc_balance = usdc_contract.functions.balanceOf(address).call()
        usdc_amount = usdc_balance / 1e6  # USDC tiene 6 decimales
        print(f"   Balance USDC: ${usdc_amount:.2f}")

        # Aprobar máximo para CTF Exchange
        MAX_UINT = 2**256 - 1

        # Verificar si ya está aprobado
        current_allowance = usdc_contract.functions.allowance(
            address, w3.to_checksum_address(CTF_EXCHANGE)
        ).call()

        if current_allowance > 0:
            print("   ✅ USDC ya aprobado para Polymarket Exchange")
        else:
            print("   Aprobando USDC para Polymarket Exchange...")
            tx = usdc_contract.functions.approve(
                w3.to_checksum_address(CTF_EXCHANGE),
                MAX_UINT
            ).build_transaction({
                'from': address,
                'nonce': w3.eth.get_transaction_count(address),
                'gas': 100000,
                'gasPrice': w3.eth.gas_price,
                'chainId': 137
            })

            signed = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"   TX enviada: {tx_hash.hex()}")
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status == 1:
                print("   ✅ USDC aprobado para CTF Exchange")
            else:
                print("   ❌ Error en la transacción de aprobación")

        # Aprobar para Neg Risk Exchange
        current_allowance_nr = usdc_contract.functions.allowance(
            address, w3.to_checksum_address(NEG_RISK_EXCHANGE)
        ).call()

        if current_allowance_nr > 0:
            print("   ✅ USDC ya aprobado para Neg Risk Exchange")
        else:
            print("   Aprobando USDC para Neg Risk Exchange...")
            tx2 = usdc_contract.functions.approve(
                w3.to_checksum_address(NEG_RISK_EXCHANGE),
                MAX_UINT
            ).build_transaction({
                'from': address,
                'nonce': w3.eth.get_transaction_count(address),
                'gas': 100000,
                'gasPrice': w3.eth.gas_price,
                'chainId': 137
            })

            signed2 = w3.eth.account.sign_transaction(tx2, private_key)
            tx_hash2 = w3.eth.send_raw_transaction(signed2.raw_transaction)
            print(f"   TX enviada: {tx_hash2.hex()}")
            receipt2 = w3.eth.wait_for_transaction_receipt(tx_hash2, timeout=60)
            if receipt2.status == 1:
                print("   ✅ USDC aprobado para Neg Risk Exchange")
            else:
                print("   ❌ Error en la transacción")

        return True

    except Exception as e:
        print(f"❌ Error en aprobaciones: {e}")
        return False


def step2b_approve_token_transfers():
    """Aprueba transfers de outcome tokens para poder VENDER posiciones."""
    print("\n" + "=" * 50)
    print("PASO 2B: Aprobar Venta de Tokens (setApprovalForAll)")
    print("=" * 50)

    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not private_key:
        print("❌ Clave privada no configurada")
        return False

    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider("https://polygon-bor-rpc.publicnode.com"))
        if not w3.is_connected():
            w3 = Web3(Web3.HTTPProvider("https://1rpc.io/matic"))

        account = w3.eth.account.from_key(private_key)
        address = account.address

        CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
        NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
        NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

        ERC1155_ABI = [
            {
                "inputs": [
                    {"name": "operator", "type": "address"},
                    {"name": "approved", "type": "bool"}
                ],
                "name": "setApprovalForAll",
                "outputs": [],
                "type": "function"
            },
            {
                "inputs": [
                    {"name": "account", "type": "address"},
                    {"name": "operator", "type": "address"}
                ],
                "name": "isApprovedForAll",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function"
            }
        ]

        ctf = w3.eth.contract(
            address=w3.to_checksum_address(CTF_ADDRESS),
            abi=ERC1155_ABI
        )

        nonce = w3.eth.get_transaction_count(address)

        operators = [
            ("CTF Exchange", CTF_EXCHANGE),
            ("Neg Risk Exchange", NEG_RISK_EXCHANGE),
            ("Neg Risk Adapter", NEG_RISK_ADAPTER),
        ]

        for name, operator in operators:
            op_addr = w3.to_checksum_address(operator)
            is_approved = ctf.functions.isApprovedForAll(address, op_addr).call()

            if is_approved:
                print(f"   ✅ Tokens ya aprobados para {name}")
            else:
                print(f"   Aprobando tokens para {name}...")
                tx = ctf.functions.setApprovalForAll(
                    op_addr, True
                ).build_transaction({
                    'from': address,
                    'nonce': nonce,
                    'gas': 80000,
                    'gasPrice': w3.eth.gas_price,
                    'chainId': 137
                })
                signed = w3.eth.account.sign_transaction(tx, private_key)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                if receipt.status == 1:
                    print(f"   ✅ Aprobado para {name} | TX: {tx_hash.hex()[:20]}...")
                    nonce += 1
                else:
                    print(f"   ❌ Error aprobando {name}")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def step3_update_env(api_keys: dict):
    """Actualiza el archivo .env con las nuevas API keys."""
    print("\n" + "=" * 50)
    print("PASO 3: Guardar API Keys en .env")
    print("=" * 50)

    env_path = os.path.join(os.path.dirname(__file__), ".env")

    try:
        with open(env_path, "r") as f:
            content = f.read()

        # Reemplazar las keys
        lines = content.split("\n")
        new_lines = []
        for line in lines:
            if line.startswith("POLYMARKET_API_KEY="):
                new_lines.append(f"POLYMARKET_API_KEY={api_keys['api_key']}")
            elif line.startswith("POLYMARKET_SECRET="):
                new_lines.append(f"POLYMARKET_SECRET={api_keys['api_secret']}")
            elif line.startswith("POLYMARKET_PASSPHRASE="):
                new_lines.append(f"POLYMARKET_PASSPHRASE={api_keys['api_passphrase']}")
            else:
                new_lines.append(line)

        with open(env_path, "w") as f:
            f.write("\n".join(new_lines))

        print("   ✅ API keys guardadas en .env")
        print("\n   Tu archivo .env ahora tiene:")
        print(f"   POLYMARKET_API_KEY={api_keys['api_key']}")
        print(f"   POLYMARKET_SECRET={api_keys['api_secret'][:20]}...")
        print(f"   POLYMARKET_PASSPHRASE={api_keys['api_passphrase'][:20]}...")
        return True

    except Exception as e:
        print(f"❌ Error guardando en .env: {e}")
        print("   Copia las keys manualmente al archivo .env")
        return False


def main():
    print("\n🤖 PolyBot - CONFIGURACIÓN INICIAL")
    print("=" * 50)
    print("Este script configura todo lo necesario para operar.")
    print("Solo necesitas correrlo UNA VEZ.\n")

    # Verificar que .env existe
    if not os.path.exists(".env"):
        print("❌ No se encontró archivo .env")
        print("   Ejecuta: copy .env.example .env")
        print("   Y agrega tu clave privada")
        return

    # Paso 1: Generar API keys
    api_keys = step1_generate_api_keys()
    if not api_keys:
        print("\n⚠️ No se pudieron generar las API keys.")
        print("   Verifica tu clave privada en .env y vuelve a intentar.")
        return

    # Paso 2: Aprobar contratos (USDC para comprar)
    approved = step2_approve_contracts()
    if not approved:
        print("\n⚠️ No se pudieron aprobar los contratos.")
        print("   Asegúrate de tener POL para gas y USDC en tu wallet.")

    # Paso 2B: Aprobar tokens (para poder VENDER posiciones)
    step2b_approve_token_transfers()

    # Paso 3: Guardar en .env
    step3_update_env(api_keys)

    print("\n" + "=" * 50)
    print("🎉 ¡CONFIGURACIÓN COMPLETADA!")
    print("=" * 50)
    print("\nTu bot está listo para operar. Próximos pasos:")
    print("  1. Deposita USDC en Polymarket desde la web")
    print("  2. Prueba con: python main.py --once")
    print("  3. Cuando estés listo: python main.py --live")
    print()


if __name__ == "__main__":
    main()
