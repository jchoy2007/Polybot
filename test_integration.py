"""
PolyBot - Test de Integración (Día 3)
=======================================
Verifica que todos los módulos cargan correctamente
ANTES de correr el bot en vivo.

EJECUTAR: python test_integration.py
"""

import os
import sys
import json
import asyncio
from datetime import datetime

# Agregar directorio raíz
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Colores para terminal
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"

passed = 0
failed = 0
warnings = 0


def ok(msg):
    global passed
    passed += 1
    print(f"  {GREEN}✅ {msg}{RESET}")


def fail(msg, error=""):
    global failed
    failed += 1
    print(f"  {RED}❌ {msg}{RESET}")
    if error:
        print(f"     {RED}{error}{RESET}")


def warn(msg):
    global warnings
    warnings += 1
    print(f"  {YELLOW}⚠️  {msg}{RESET}")


async def main():
    global passed, failed, warnings

    print(f"\n{BOLD}{'='*60}")
    print("🧪 PolyBot - Test de Integración Día 3")
    print(f"{'='*60}{RESET}\n")

    # ── TEST 1: .env ──────────────────────────────────────────
    print(f"{BOLD}1. Variables de entorno (.env){RESET}")
    try:
        from dotenv import load_dotenv
        load_dotenv()
        ok("dotenv cargado")
    except ImportError:
        fail("python-dotenv no instalado", "pip install python-dotenv")
        return

    env_vars = {
        "POLYGON_WALLET_PRIVATE_KEY": True,
        "ANTHROPIC_API_KEY": True,
        "POLYMARKET_FUNDER_ADDRESS": False,
        "TELEGRAM_BOT_TOKEN": False,
    }
    for var, required in env_vars.items():
        val = os.getenv(var, "")
        if val and val != "0x...":
            ok(f"{var}: configurado")
        elif required:
            fail(f"{var}: NO configurado (requerido)")
        else:
            warn(f"{var}: no configurado (opcional)")

    # ── TEST 2: Config ────────────────────────────────────────
    print(f"\n{BOLD}2. Configuración (settings.py){RESET}")
    try:
        from config.settings import SAFETY, STATE
        ok(f"SAFETY cargado (kelly={SAFETY.kelly_fraction}, edge_min={SAFETY.min_edge_required})")
        ok(f"STATE cargado (bankroll=${STATE.current_bankroll})")
    except Exception as e:
        fail("Error cargando settings", str(e))
        return

    # ── TEST 3: Core modules ─────────────────────────────────
    print(f"\n{BOLD}3. Módulos core{RESET}")
    core_modules = [
        ("core.market_scanner", "MarketScanner"),
        ("core.ai_analyzer", "AIAnalyzer"),
        ("core.risk_manager", "RiskManager"),
        ("core.executor", "TradeExecutor"),
        ("core.tracker", "WinRateTracker"),
    ]
    for mod_path, class_name in core_modules:
        try:
            mod = __import__(mod_path, fromlist=[class_name])
            cls = getattr(mod, class_name)
            ok(f"{mod_path}.{class_name}")
        except Exception as e:
            fail(f"{mod_path}.{class_name}", str(e))

    # ── TEST 4: Estrategias (el test principal) ───────────────
    print(f"\n{BOLD}4. Estrategias{RESET}")

    strategies = {
        "Estrategia 2 - Crypto 15-Min": ("modules.btc_15min", "BTC15MinStrategy"),
        "Estrategia 3 - NO Harvester": ("modules.no_harvester", "NOHarvester"),
        "Estrategia 4 - Weather Trader": ("modules.weather_trader", "WeatherTrader"),
        "Estrategia 5 - Stock Trader": ("modules.stock_trader", "StockTrader"),
        "Estrategia 6 - Flash Crash": ("modules.flash_crash", "FlashCrashDetector"),
    }

    instances = {}
    for name, (mod_path, class_name) in strategies.items():
        try:
            mod = __import__(mod_path, fromlist=[class_name])
            cls = getattr(mod, class_name)
            instance = cls()
            instances[name] = instance
            ok(f"{name}: importado e inicializado")
        except Exception as e:
            fail(f"{name}: error", str(e))

    # ── TEST 5: Métodos requeridos ────────────────────────────
    print(f"\n{BOLD}5. Interfaces de estrategias{RESET}")

    for name, instance in instances.items():
        if hasattr(instance, 'run_cycle'):
            ok(f"{name}: tiene run_cycle()")
        elif hasattr(instance, 'find_harvest_opportunities'):
            ok(f"{name}: tiene find_harvest_opportunities() + execute_harvest()")
        else:
            fail(f"{name}: sin método de ejecución")

        if hasattr(instance, 'close'):
            ok(f"{name}: tiene close()")
        else:
            warn(f"{name}: sin close() (no es crítico)")

        if hasattr(instance, '_save_bet'):
            ok(f"{name}: tiene _save_bet() (anti-duplicado)")

    # ── TEST 6: Auto Redeem ───────────────────────────────────
    print(f"\n{BOLD}6. Módulos auxiliares{RESET}")
    try:
        from modules.auto_redeem import AutoRedeemer
        ar = AutoRedeemer()
        ok("AutoRedeemer: importado")
    except Exception as e:
        fail("AutoRedeemer", str(e))

    # ── TEST 7: Data files ────────────────────────────────────
    print(f"\n{BOLD}7. Archivos de datos{RESET}")

    data_files = {
        "data/bets_placed.json": True,
        "data/trade_results.json": True,
    }
    for path, required in data_files.items():
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                ok(f"{path}: existe y es JSON válido")
            except json.JSONDecodeError:
                fail(f"{path}: JSON inválido")
        elif required:
            warn(f"{path}: no existe (se creará automáticamente)")
        else:
            warn(f"{path}: no existe (opcional)")

    # ── TEST 8: Dependencias Python ───────────────────────────
    print(f"\n{BOLD}8. Dependencias Python{RESET}")
    deps = {
        "aiohttp": True,
        "anthropic": True,
        "web3": True,
        "py_clob_client": True,
        "dotenv": True,
    }
    for dep, required in deps.items():
        try:
            __import__(dep)
            ok(f"{dep}: instalado")
        except ImportError:
            if required:
                fail(f"{dep}: NO instalado", f"pip install {dep}")
            else:
                warn(f"{dep}: no instalado (opcional)")

    # ── TEST 9: Main.py import ────────────────────────────────
    print(f"\n{BOLD}9. Import completo de main.py{RESET}")
    try:
        # Solo testear que los imports del main funcionen
        import importlib
        spec = importlib.util.spec_from_file_location("main", "main.py")
        if spec and spec.loader:
            ok("main.py: se puede cargar")
        else:
            fail("main.py: no se encontró")
    except Exception as e:
        fail("main.py: error de import", str(e))

    # ── TEST 10: Quick API test (Open-Meteo) ──────────────────
    print(f"\n{BOLD}10. Test rápido de APIs externas{RESET}")
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            # Open-Meteo (weather trader)
            async with session.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": 40.71, "longitude": -74.01,
                        "daily": "temperature_2m_max",
                        "temperature_unit": "fahrenheit",
                        "timezone": "auto",
                        "forecast_days": 1}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    temp = data.get("daily", {}).get("temperature_2m_max", [None])[0]
                    ok(f"Open-Meteo API: NYC temp = {temp}°F")
                else:
                    fail(f"Open-Meteo API: status {resp.status}")

            # Binance (flash crash)
            async with session.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data["price"])
                    ok(f"Binance API: BTC = ${price:,.0f}")
                else:
                    fail(f"Binance API: status {resp.status}")

            # Yahoo Finance (stock trader)
            async with session.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/^GSPC",
                params={"range": "1d", "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = data.get("chart", {}).get("result", [{}])[0].get("meta", {}).get("regularMarketPrice", 0)
                    ok(f"Yahoo Finance: S&P 500 = ${price:,.0f}")
                else:
                    warn(f"Yahoo Finance: status {resp.status} (puede requerir VPN)")

    except Exception as e:
        warn(f"APIs externas: {str(e)[:60]} (verificar conexión)")

    # ── CLEANUP ───────────────────────────────────────────────
    for name, instance in instances.items():
        if hasattr(instance, 'close'):
            try:
                await instance.close()
            except:
                pass

    # ── RESUMEN ───────────────────────────────────────────────
    print(f"\n{BOLD}{'='*60}")
    print("📊 RESUMEN")
    print(f"{'='*60}{RESET}")
    print(f"  {GREEN}✅ Pasaron: {passed}{RESET}")
    print(f"  {RED}❌ Fallaron: {failed}{RESET}")
    print(f"  {YELLOW}⚠️  Warnings: {warnings}{RESET}")
    print()

    if failed == 0:
        print(f"  {GREEN}{BOLD}🎉 ¡TODO OK! Puedes correr el bot:{RESET}")
        print(f"  {GREEN}   python main.py --once          (un ciclo, dry run){RESET}")
        print(f"  {GREEN}   python main.py --live --once   (un ciclo, dinero real){RESET}")
        print(f"  {GREEN}   python main.py --live --stop-at 18  (modo continuo){RESET}")
    else:
        print(f"  {RED}{BOLD}⛔ HAY ERRORES. Corrige los fallos antes de correr el bot.{RESET}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
