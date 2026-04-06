"""
PolyBot - Health Check Completo
=================================
Verifica que TODO funcione: APIs, wallet, estrategias, datos.

USO: python health_check.py
CUÁNDO: Después de cada sesión o cuando quieras verificar estado.
"""

import os
import sys
import json
import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# Fix Windows encoding para emojis
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

G = "\033[92m"   # Green
R = "\033[91m"   # Red
Y = "\033[93m"   # Yellow
B = "\033[94m"   # Blue
W = "\033[97m"   # White
D = "\033[0m"    # Reset
BOLD = "\033[1m"

results = {"ok": 0, "fail": 0, "warn": 0}


def ok(msg, detail=""):
    results["ok"] += 1
    d = f" → {detail}" if detail else ""
    print(f"  {G}✅ {msg}{d}{D}")

def fail(msg, detail=""):
    results["fail"] += 1
    d = f" → {detail}" if detail else ""
    print(f"  {R}❌ {msg}{d}{D}")

def warn(msg, detail=""):
    results["warn"] += 1
    d = f" → {detail}" if detail else ""
    print(f"  {Y}⚠️  {msg}{d}{D}")

def section(title):
    print(f"\n{BOLD}{B}{'─'*50}{D}")
    print(f"{BOLD}{B}  {title}{D}")
    print(f"{BOLD}{B}{'─'*50}{D}")


async def main():
    print(f"\n{BOLD}{W}{'═'*50}")
    print(f"  🏥 PolyBot - Health Check Completo")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*50}{D}")

    import aiohttp

    # ═══════════════════════════════════════════════════
    section("1. WALLET Y BALANCE")
    # ═══════════════════════════════════════════════════

    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not pk or pk == "0x...":
        fail("Private key no configurada")
    else:
        ok("Private key configurada")

    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    if funder:
        ok("Funder address configurada", funder[:10] + "...")
    else:
        warn("Funder address no configurada (opcional)")

    # Balance on-chain
    balance = 0
    try:
        from web3 import Web3
        rpcs = ["https://polygon-bor-rpc.publicnode.com",
                "https://1rpc.io/matic", "https://polygon-rpc.com"]
        w3 = None
        for rpc in rpcs:
            try:
                w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
                if w3.is_connected():
                    break
            except:
                continue

        if w3 and w3.is_connected():
            ok("Polygon RPC conectado")
            account = w3.eth.account.from_key(pk)
            USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            abi = [{"inputs":[{"name":"account","type":"address"}],
                    "name":"balanceOf",
                    "outputs":[{"name":"","type":"uint256"}],
                    "type":"function"}]
            usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E), abi=abi)
            balance = usdc.functions.balanceOf(account.address).call() / 1e6
            ok(f"Balance USDC.e: ${balance:.2f}")

            # MATIC para gas
            matic = w3.eth.get_balance(account.address) / 1e18
            if matic > 0.01:
                ok(f"MATIC para gas: {matic:.4f}")
            else:
                warn(f"MATIC bajo: {matic:.4f} (necesitas gas para transacciones)")
        else:
            fail("No se pudo conectar a Polygon RPC")
    except Exception as e:
        fail(f"Error wallet: {str(e)[:60]}")

    # ═══════════════════════════════════════════════════
    section("2. POLYMARKET CLOB (ejecutar trades)")
    # ═══════════════════════════════════════════════════

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        pk_clean = pk[2:] if pk.startswith("0x") else pk
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=pk_clean, chain_id=137, signature_type=0
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        ok("CLOB client conectado")

        # Balance en CLOB
        bal_resp = client.get_balance_allowance(
            params=BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=0
            )
        )
        clob_balance = float(bal_resp.get("balance", "0")) / 1e6
        allowance = float(bal_resp.get("allowance", "0")) / 1e6
        ok(f"Balance CLOB: ${clob_balance:.2f}")
        if allowance > 0:
            ok(f"Allowance: ${allowance:.2f}")
        else:
            warn("Allowance = 0 (puede fallar al ejecutar trades)")

        # Test API creds
        try:
            orders = client.get_orders()
            ok("API creds funcionan (get_orders OK)")
        except Exception as e:
            warn(f"API creds parcial: {str(e)[:50]}")

    except Exception as e:
        fail(f"Error CLOB: {str(e)[:60]}")

    # ═══════════════════════════════════════════════════
    section("3. POSICIONES ACTIVAS")
    # ═══════════════════════════════════════════════════

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            address = Web3().eth.account.from_key(pk).address
            addrs = [address.lower()]
            if funder:
                addrs.append(funder.lower())

            positions = []
            for addr in addrs:
                try:
                    async with session.get(
                        f"https://data-api.polymarket.com/positions?user={addr}"
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data and isinstance(data, list):
                                positions = data
                                break
                except:
                    continue

            if positions:
                active = [p for p in positions
                          if float(p.get("currentValue", 0) or 0) > 0.01]
                total_value = sum(float(p.get("currentValue", 0) or 0) for p in active)
                redeemable = [p for p in active
                              if float(p.get("curPrice", 0) or 0) >= 0.99]

                ok(f"{len(active)} posiciones activas (${total_value:.2f})")
                if redeemable:
                    red_value = sum(float(p.get("currentValue", 0) or 0) for p in redeemable)
                    warn(f"{len(redeemable)} posiciones COBRABLES → python redeem.py (${red_value:.2f})")
                else:
                    ok("Ninguna posición lista para cobrar ahora")
            else:
                ok("Sin posiciones activas")
    except Exception as e:
        warn(f"Error buscando posiciones: {str(e)[:50]}")

    # ═══════════════════════════════════════════════════
    section("4. APIs EXTERNAS")
    # ═══════════════════════════════════════════════════

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:

        # Polymarket Gamma API
        try:
            async with session.get(
                "https://gamma-api.polymarket.com/markets?limit=1&active=true"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ok(f"Polymarket Gamma API OK ({len(data)} mercado)")
                else:
                    fail(f"Polymarket Gamma API: status {resp.status}")
        except Exception as e:
            fail(f"Polymarket Gamma API: {str(e)[:40]}")

        # Binance (crypto strategy + flash crash)
        try:
            async with session.get(
                "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    btc = float(data["price"])
                    ok(f"Binance API OK (BTC=${btc:,.0f})")
                else:
                    fail(f"Binance API: status {resp.status}")
        except Exception as e:
            fail(f"Binance API: {str(e)[:40]}")

        # Open-Meteo (weather strategy)
        try:
            async with session.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": 40.71, "longitude": -74.01,
                        "daily": "temperature_2m_max",
                        "temperature_unit": "fahrenheit",
                        "timezone": "auto", "forecast_days": 1}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    temp = data.get("daily", {}).get("temperature_2m_max", [None])[0]
                    ok(f"Open-Meteo API OK (NYC={temp}°F)")
                else:
                    fail(f"Open-Meteo API: status {resp.status}")
        except Exception as e:
            fail(f"Open-Meteo API: {str(e)[:40]}")

        # Yahoo Finance (stock strategy)
        try:
            async with session.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/^GSPC",
                params={"range": "1d", "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sp = data.get("chart", {}).get("result", [{}])[0].get(
                        "meta", {}).get("regularMarketPrice", 0)
                    ok(f"Yahoo Finance OK (S&P 500=${sp:,.0f})")
                else:
                    warn(f"Yahoo Finance: status {resp.status}")
        except Exception as e:
            warn(f"Yahoo Finance: {str(e)[:40]}")

        # Anthropic API (IA strategy)
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if api_key:
            try:
                import anthropic
                client_ai = anthropic.Anthropic(api_key=api_key)
                # Solo verificar que la key es válida con un request mínimo
                resp = client_ai.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "ping"}]
                )
                if resp and resp.content:
                    ok("Anthropic API OK (Claude Haiku)")
                else:
                    fail("Anthropic API: respuesta vacía")
            except Exception as e:
                err = str(e)
                if "credit" in err.lower() or "billing" in err.lower() or "402" in err:
                    fail("Anthropic API: SIN CRÉDITO ($0 restante)")
                elif "invalid" in err.lower() or "401" in err:
                    fail("Anthropic API: key inválida")
                else:
                    fail(f"Anthropic API: {err[:50]}")
        else:
            fail("Anthropic API key no configurada")

    # ═══════════════════════════════════════════════════
    section("5. MÓDULOS DEL BOT")
    # ═══════════════════════════════════════════════════

    modules_to_check = [
        ("core.market_scanner", "MarketScanner", "Scanner"),
        ("core.ai_analyzer", "AIAnalyzer", "IA Analyzer"),
        ("core.risk_manager", "RiskManager", "Risk Manager"),
        ("core.executor", "TradeExecutor", "Executor"),
        ("core.tracker", "WinRateTracker", "Tracker"),
        ("modules.btc_15min", "BTC15MinStrategy", "Crypto 15-Min"),
        ("modules.no_harvester", "NOHarvester", "NO Harvester"),
        ("modules.weather_trader", "WeatherTrader", "Weather Trader"),
        ("modules.stock_trader", "StockTrader", "Stock Trader"),
        ("modules.flash_crash", "FlashCrashDetector", "Flash Crash"),
        ("modules.auto_redeem", "AutoRedeemer", "Auto Redeem"),
    ]

    for mod_path, class_name, label in modules_to_check:
        try:
            mod = __import__(mod_path, fromlist=[class_name])
            cls = getattr(mod, class_name)
            instance = cls() if class_name not in ["TradeExecutor", "AIAnalyzer", "MarketScanner"] else None
            ok(f"{label}")
        except Exception as e:
            fail(f"{label}: {str(e)[:50]}")

    # ═══════════════════════════════════════════════════
    section("6. DATOS PERSISTENTES")
    # ═══════════════════════════════════════════════════

    # bets_placed.json
    try:
        with open("data/bets_placed.json", "r") as f:
            bets = json.load(f)
            count = len(bets.get("market_ids", []))
            ok(f"Anti-duplicado: {count} mercados registrados")
    except FileNotFoundError:
        warn("bets_placed.json no existe (se creará al primer trade)")
    except json.JSONDecodeError:
        fail("bets_placed.json corrupto")

    # trade_results.json
    try:
        with open("data/trade_results.json", "r") as f:
            trades = json.load(f)
            won = sum(1 for t in trades if t.get("result") == "WON")
            lost = sum(1 for t in trades if t.get("result") == "LOST")
            pending = sum(1 for t in trades if t.get("result") == "PENDING")
            ok(f"Trades: {won}W / {lost}L / {pending}P ({len(trades)} total)")
    except FileNotFoundError:
        warn("trade_results.json no existe (se creará al primer trade)")
    except json.JSONDecodeError:
        fail("trade_results.json corrupto")

    # flash_prices.json
    try:
        with open("data/flash_prices.json", "r") as f:
            flash = json.load(f)
            markets = len(flash.get("markets", {}))
            ok(f"Flash prices: {markets} mercados tracked")
    except FileNotFoundError:
        ok("flash_prices.json no existe aún (normal en primer ciclo)")
    except json.JSONDecodeError:
        warn("flash_prices.json corrupto")

    # Logs
    today = datetime.now().strftime("%Y%m%d")
    log_file = Path("logs") / f"polybot_{today}.log"
    if log_file.exists():
        size = log_file.stat().st_size / 1024
        ok(f"Log de hoy: {size:.0f} KB")
    else:
        warn("Sin log de hoy")

    # ═══════════════════════════════════════════════════
    section("7. RESUMEN DE CAPITAL")
    # ═══════════════════════════════════════════════════

    print(f"\n  {W}Balance libre:     ${balance:.2f}{D}")
    try:
        total_pos = sum(float(p.get("currentValue", 0) or 0)
                        for p in positions if float(p.get("currentValue", 0) or 0) > 0.01)
        print(f"  {W}En posiciones:     ${total_pos:.2f}{D}")
        print(f"  {W}{'─'*30}{D}")
        print(f"  {BOLD}{W}Total estimado:    ${balance + total_pos:.2f}{D}")
    except:
        pass

    # ═══════════════════════════════════════════════════
    # RESULTADO FINAL
    # ═══════════════════════════════════════════════════
    print(f"\n{BOLD}{'═'*50}")
    print(f"  📊 RESULTADO{D}")
    print(f"{BOLD}{'═'*50}{D}")
    print(f"  {G}✅ OK: {results['ok']}{D}")
    print(f"  {R}❌ Fallos: {results['fail']}{D}")
    print(f"  {Y}⚠️  Warnings: {results['warn']}{D}")
    print()

    if results["fail"] == 0:
        print(f"  {G}{BOLD}🎉 TODO SANO. Bot listo para operar.{D}")
        print(f"  {G}   python main.py --live --stop-at 18{D}")
    elif results["fail"] <= 2:
        print(f"  {Y}{BOLD}⚠️ CASI LISTO. Revisa los fallos antes de correr.{D}")
    else:
        print(f"  {R}{BOLD}⛔ HAY PROBLEMAS. Corrige los fallos.{D}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
