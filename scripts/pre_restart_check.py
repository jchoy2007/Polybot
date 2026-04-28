#!/usr/bin/env python3
"""
Pre-restart sanity check.

Uso:
    cd /root/Polybot && ./venv/bin/python scripts/pre_restart_check.py
    # Exit 0 si todo OK, exit 1 si algo falla.

Valida:
1. Sintaxis Python de main.py, core/*.py, modules/*.py
2. ENV vars requeridas están seteadas
3. data/ es escribible
4. ./venv/bin/python existe y es funcional
5. Wallet accesible y balance USDC.e > 0
"""
from __future__ import annotations

import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / "venv" / "bin" / "python"
REQUIRED_ENVS = (
    "POLYGON_WALLET_PRIVATE_KEY",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)

OK = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"

errors: list[str] = []


def check_syntax() -> None:
    files: list[Path] = [ROOT / "main.py"]
    for sub in ("core", "modules"):
        files.extend((ROOT / sub).glob("*.py"))
    for f in files:
        if not f.exists():
            continue
        try:
            py_compile.compile(str(f), doraise=True)
            print(f"  {OK} {f.relative_to(ROOT)}")
        except py_compile.PyCompileError as e:
            print(f"  {FAIL} {f.relative_to(ROOT)}: {e.msg.splitlines()[0]}")
            errors.append(f"syntax: {f.name}")


def check_envs() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    for var in REQUIRED_ENVS:
        val = os.getenv(var)
        if val:
            print(f"  {OK} {var} (len={len(val)})")
        else:
            print(f"  {FAIL} {var} vacía o ausente")
            errors.append(f"env: {var}")


def check_data_writable() -> None:
    data_dir = ROOT / "data"
    if not data_dir.is_dir():
        print(f"  {FAIL} data/ no existe")
        errors.append("data/ missing")
        return
    try:
        with tempfile.NamedTemporaryFile(dir=data_dir, delete=True):
            pass
        print(f"  {OK} data/ escribible")
    except OSError as e:
        print(f"  {FAIL} data/ no escribible: {e}")
        errors.append("data/ not writable")


def check_venv() -> None:
    if not VENV_PY.exists():
        print(f"  {FAIL} {VENV_PY} no existe")
        errors.append("venv missing")
        return
    try:
        r = subprocess.run(
            [str(VENV_PY), "-c", "import aiohttp, web3, anthropic; print('OK')"],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0 and "OK" in r.stdout:
            print(f"  {OK} venv python importa dependencias clave")
        else:
            print(f"  {FAIL} venv import failed: {r.stderr.strip()[:120]}")
            errors.append("venv deps")
    except Exception as e:
        print(f"  {FAIL} venv run failed: {e}")
        errors.append("venv run")


def check_wallet() -> None:
    try:
        from dotenv import load_dotenv
        from web3 import Web3
        load_dotenv(ROOT / ".env")
        rpc = os.getenv("ALCHEMY_RPC_URL") or "https://polygon-bor-rpc.publicnode.com"
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
        addr = w3.eth.account.from_key(os.getenv("POLYGON_WALLET_PRIVATE_KEY")).address
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        target = w3.to_checksum_address(funder) if funder else addr
        abi = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
        pusd = w3.eth.contract(
            address=w3.to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"),
            abi=abi,
        )
        bal = pusd.functions.balanceOf(target).call() / 1e6
        if bal > 0:
            print(f"  {OK} funder {target[:6]}… pUSD ${bal:.2f}")
        else:
            print(f"  {FAIL} pUSD balance = 0 (target {target})")
            errors.append("wallet balance 0")
    except Exception as e:
        print(f"  {FAIL} wallet check falló: {e}")
        errors.append("wallet access")


def main() -> int:
    print("=" * 60)
    print("  PRE-RESTART CHECK")
    print("=" * 60)
    print("\n1. Sintaxis Python:")
    check_syntax()
    print("\n2. Variables de entorno:")
    check_envs()
    print("\n3. Directorio data/:")
    check_data_writable()
    print("\n4. Venv:")
    check_venv()
    print("\n5. Wallet USDC.e:")
    check_wallet()
    print("\n" + "=" * 60)
    if errors:
        print(f"  ❌ FAIL — {len(errors)} problemas: {', '.join(errors)}")
        return 1
    print("  ✅ OK — seguro hacer systemctl restart polybot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
