"""
Unwrap WrappedCollateral → USDC.e
"""
import os, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
from web3 import Web3
load_dotenv()

WCOL_ADDRESS = "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# ABI con ambas versiones de unwrap
WCOL_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    # unwrap(address _to, uint256 _amount)
    {"inputs":[{"name":"_to","type":"address"},{"name":"_amount","type":"uint256"}],"name":"unwrap","outputs":[],"type":"function","stateMutability":"nonpayable"},
]
WCOL_ABI_V2 = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    # unwrap(uint256 _amount) - version alternativa
    {"inputs":[{"name":"_amount","type":"uint256"}],"name":"unwrap","outputs":[],"type":"function","stateMutability":"nonpayable"},
]
ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
]

w3 = None
for rpc in ["https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic"]:
    try:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
        if w3.is_connected(): break
    except: continue

pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
eoa = w3.eth.account.from_key(pk).address
usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E_ADDRESS), abi=ERC20_ABI)

usdc_before = usdc.functions.balanceOf(eoa).call() / 1e6

# Intentar con ABI v1: unwrap(address, uint256)
print(f"\n  Wallet: {eoa}")
wcol = w3.eth.contract(address=w3.to_checksum_address(WCOL_ADDRESS), abi=WCOL_ABI)
balance = wcol.functions.balanceOf(eoa).call()
print(f"  WCOL balance: {balance / 1e6:.2f}")
print(f"  USDC.e antes: ${usdc_before:.2f}")

if balance <= 0:
    print("  No hay WCOL para unwrap")
    sys.exit()

# Metodo 1: unwrap(address, uint256)
print(f"\n  Intentando unwrap(address, amount)...")
try:
    nonce = w3.eth.get_transaction_count(eoa)
    txn = wcol.functions.unwrap(eoa, balance).build_transaction({
        'from': eoa, 'nonce': nonce, 'gas': 200000,
        'gasPrice': w3.eth.gas_price, 'chainId': 137
    })
    signed = w3.eth.account.sign_transaction(txn, pk)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    time.sleep(3)
    usdc_after = usdc.functions.balanceOf(eoa).call() / 1e6
    diff = round(usdc_after - usdc_before, 2)
    print(f"  TX status: {receipt.status}")
    print(f"  USDC.e despues: ${usdc_after:.2f}")
    print(f"  Diferencia: ${diff:+.2f}")
    if diff > 0:
        print(f"\n  EXITO! +${diff:.2f} cobrado!")
        sys.exit()
except Exception as e:
    print(f"  Error: {str(e)[:80]}")

# Metodo 2: unwrap(uint256)
print(f"\n  Intentando unwrap(amount)...")
try:
    wcol2 = w3.eth.contract(address=w3.to_checksum_address(WCOL_ADDRESS), abi=WCOL_ABI_V2)
    balance2 = wcol2.functions.balanceOf(eoa).call()
    nonce = w3.eth.get_transaction_count(eoa)
    txn = wcol2.functions.unwrap(balance2).build_transaction({
        'from': eoa, 'nonce': nonce, 'gas': 200000,
        'gasPrice': w3.eth.gas_price, 'chainId': 137
    })
    signed = w3.eth.account.sign_transaction(txn, pk)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    time.sleep(3)
    usdc_after = usdc.functions.balanceOf(eoa).call() / 1e6
    diff = round(usdc_after - usdc_before, 2)
    print(f"  TX status: {receipt.status}")
    print(f"  USDC.e despues: ${usdc_after:.2f}")
    print(f"  Diferencia: ${diff:+.2f}")
    if diff > 0:
        print(f"\n  EXITO! +${diff:.2f} cobrado!")
except Exception as e:
    print(f"  Error: {str(e)[:80]}")

# Metodo 3: burn(address, uint256) - quizas burn libera USDC
print(f"\n  Intentando burn...")
try:
    BURN_ABI = [
        {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
        {"inputs":[{"name":"_to","type":"address"},{"name":"_amount","type":"uint256"}],"name":"burn","outputs":[],"type":"function","stateMutability":"nonpayable"},
    ]
    wcol3 = w3.eth.contract(address=w3.to_checksum_address(WCOL_ADDRESS), abi=BURN_ABI)
    balance3 = wcol3.functions.balanceOf(eoa).call()
    if balance3 > 0:
        nonce = w3.eth.get_transaction_count(eoa)
        txn = wcol3.functions.burn(eoa, balance3).build_transaction({
            'from': eoa, 'nonce': nonce, 'gas': 200000,
            'gasPrice': w3.eth.gas_price, 'chainId': 137
        })
        signed = w3.eth.account.sign_transaction(txn, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        time.sleep(3)
        usdc_after = usdc.functions.balanceOf(eoa).call() / 1e6
        diff = round(usdc_after - usdc_before, 2)
        print(f"  TX status: {receipt.status}")
        print(f"  Diferencia: ${diff:+.2f}")
except Exception as e:
    print(f"  Error: {str(e)[:80]}")

print(f"\n  Balance WCOL final: {wcol.functions.balanceOf(eoa).call() / 1e6:.2f}")
print(f"  Balance USDC final: ${usdc.functions.balanceOf(eoa).call() / 1e6:.2f}")
