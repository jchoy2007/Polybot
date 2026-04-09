"""
PolyBot - Generador de Reporte Diario
=======================================
Genera un reporte completo del día en data/report_YYYYMMDD.txt
que puedes copiar y pegar para análisis.

Se ejecuta automáticamente al final del día (auto-stop) o manual:
    python daily_report.py

También se llama desde main.py al finalizar.
"""

import os
import sys
import json
import asyncio
import aiohttp
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = "data"
LOGS_DIR = "logs"
REPORT_DIR = "data/reports"


async def generate_report() -> str:
    """Genera reporte completo del día."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    lines = []

    lines.append("=" * 60)
    lines.append(f"  POLYBOT - REPORTE DIARIO")
    lines.append(f"  Fecha: {today} | Generado: {now.strftime('%H:%M:%S')}")
    lines.append("=" * 60)

    # === 1. BALANCE ACTUAL ===
    balance = 0
    try:
        from web3 import Web3
        rpcs = ["https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic",
                "https://polygon-rpc.com"]
        pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
        if pk:
            for rpc in rpcs:
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
                    if w3.is_connected():
                        USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
                        abi = [{"inputs": [{"name": "account", "type": "address"}],
                                "name": "balanceOf",
                                "outputs": [{"name": "", "type": "uint256"}],
                                "type": "function"}]
                        address = w3.eth.account.from_key(pk).address
                        usdc = w3.eth.contract(
                            address=w3.to_checksum_address(USDC_E), abi=abi)
                        balance = usdc.functions.balanceOf(address).call() / 1e6
                        break
                except:
                    continue
    except:
        pass

    lines.append(f"\n--- BALANCE ---")
    lines.append(f"  USDC.e libre: ${balance:.2f}")
    lines.append(f"  Depositado total: $200.00")

    # === 2. POSICIONES ACTIVAS ===
    positions = []
    total_position_value = 0
    try:
        pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        if pk:
            from web3 import Web3
            address = Web3().eth.account.from_key(pk).address
            addrs = [a for a in [funder, address] if a]
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                for addr in addrs:
                    try:
                        async with session.get(
                            f"https://data-api.polymarket.com/positions?user={addr.lower()}"
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data and isinstance(data, list) and len(data) > 0:
                                    positions = data
                                    break
                    except:
                        continue
    except:
        pass

    if positions:
        lines.append(f"\n--- POSICIONES ACTIVAS ({len(positions)}) ---")

        winning = []
        losing = []
        pending = []

        for pos in positions:
            title = (pos.get("title") or pos.get("question") or "?")[:50]
            side = pos.get("outcome") or "?"
            size = float(pos.get("size") or 0)
            cur_price = float(pos.get("curPrice") or 0)
            value = float(pos.get("currentValue") or 0)
            if value == 0 and size > 0 and cur_price > 0:
                value = size * cur_price
            pnl = float(pos.get("cashPnl") or 0)
            total_position_value += value

            entry = f"  {title} | {side} ${value:.2f} ({cur_price:.0%}) P&L: ${pnl:+.2f}"

            if cur_price >= 0.90:
                winning.append(entry)
            elif cur_price <= 0.30:
                losing.append(entry)
            else:
                pending.append(entry)

        if winning:
            lines.append(f"\n  GANADORAS (>90%):")
            for w in winning:
                lines.append(f"  ✅ {w}")

        if pending:
            lines.append(f"\n  EN JUEGO (30-90%):")
            for p in pending:
                lines.append(f"  ⏳ {p}")

        if losing:
            lines.append(f"\n  PERDIENDO (<30%):")
            for l in losing:
                lines.append(f"  ❌ {l}")

        lines.append(f"\n  Total en posiciones: ${total_position_value:.2f}")

    # === 3. RESUMEN FINANCIERO ===
    total_estimated = balance + total_position_value
    pnl = total_estimated - 200  # depositado

    lines.append(f"\n--- RESUMEN FINANCIERO ---")
    lines.append(f"  Balance libre:      ${balance:.2f}")
    lines.append(f"  En posiciones:      ${total_position_value:.2f}")
    lines.append(f"  Total estimado:     ${total_estimated:.2f}")
    lines.append(f"  Depositado:         $200.00")
    lines.append(f"  P&L estimado:       ${pnl:+.2f} ({pnl/200*100:+.1f}%)")

    # === 4. TRADE RESULTS ===
    try:
        with open(os.path.join(DATA_DIR, "trade_results.json"), "r") as f:
            trades = json.load(f)
    except:
        trades = []

    if trades:
        won = [t for t in trades if t.get("result") == "WON"]
        lost = [t for t in trades if t.get("result") == "LOST"]
        pend = [t for t in trades if t.get("result") == "PENDING"]

        total_resolved = len(won) + len(lost)
        win_rate = len(won) / total_resolved * 100 if total_resolved > 0 else 0
        total_profit = sum(t.get("profit", 0) for t in won)
        total_loss = sum(t.get("profit", 0) for t in lost)

        lines.append(f"\n--- WIN RATE ---")
        lines.append(f"  Ganadas: {len(won)} | Perdidas: {len(lost)} | Pendientes: {len(pend)}")
        lines.append(f"  Win Rate: {win_rate:.0f}%")
        lines.append(f"  Ganancias: ${total_profit:+.2f}")
        lines.append(f"  Pérdidas:  ${total_loss:.2f}")
        lines.append(f"  Neto:      ${total_profit + total_loss:+.2f}")

        # Por estrategia
        strategies = {}
        for t in trades:
            s = t.get("strategy", "IA")
            if s not in strategies:
                strategies[s] = {"won": 0, "lost": 0, "pending": 0, "profit": 0}
            if t["result"] == "WON":
                strategies[s]["won"] += 1
                strategies[s]["profit"] += t.get("profit", 0)
            elif t["result"] == "LOST":
                strategies[s]["lost"] += 1
                strategies[s]["profit"] += t.get("profit", 0)
            else:
                strategies[s]["pending"] += 1

        lines.append(f"\n--- POR ESTRATEGIA ---")
        for s, data in sorted(strategies.items()):
            s_total = data["won"] + data["lost"]
            s_wr = data["won"] / s_total * 100 if s_total > 0 else 0
            lines.append(
                f"  {s}: {data['won']}W/{data['lost']}L ({s_wr:.0f}%) "
                f"P&L: ${data['profit']:+.2f} | Pendientes: {data['pending']}"
            )

        # Trades de hoy
        today_trades = [t for t in trades if t.get("timestamp", "").startswith(today)]
        if today_trades:
            lines.append(f"\n--- TRADES DE HOY ({len(today_trades)}) ---")
            for t in today_trades:
                result_emoji = {"WON": "✅", "LOST": "❌", "PENDING": "⏳"}.get(t["result"], "?")
                lines.append(
                    f"  {result_emoji} [{t.get('strategy', '?')}] {t.get('question', '?')[:45]} "
                    f"| {t.get('side', '?')} ${t.get('amount', 0):.2f} @ {t.get('price', 0):.2f} "
                    f"| P&L: ${t.get('profit', 0):+.2f}"
                )

    # === 5. ANÁLISIS DEL LOG ===
    log_file = os.path.join(LOGS_DIR, f"polybot_{now.strftime('%Y%m%d')}.log")
    if os.path.exists(log_file):
        log_size = os.path.getsize(log_file)
        lines.append(f"\n--- LOG DEL BOT ---")
        lines.append(f"  Archivo: {log_file}")
        lines.append(f"  Tamaño: {log_size / 1024:.1f} KB")

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                log_content = f.read()
            log_lines = log_content.split("\n")

            # Contadores generales
            cycle_count = log_content.count("NUEVO CICLO")
            lines.append(f"  Ciclos ejecutados: {cycle_count}")

            # === TRADES EJECUTADOS ===
            executed = []
            for line in log_lines:
                if "Harvest ejecutado" in line or "FOK ejecutada" in line or "GTC ejecutada" in line:
                    # Extraer hora
                    hora = line[:8] if len(line) > 8 else ""
                    executed.append(f"    {hora} {line.split(']')[-1].strip()[:70]}" if ']' in line else f"    {line[:80]}")

            lines.append(f"  Trades ejecutados: {len(executed)}")
            if executed:
                lines.append(f"\n--- TRADES EJECUTADOS ---")
                for e in executed[-15:]:  # Últimos 15
                    lines.append(e)
                if len(executed) > 15:
                    lines.append(f"    ... y {len(executed)-15} más")

            # === SKIPS DE IA ===
            skips = [l for l in log_lines if "SKIP" in l and "recomienda" in l]
            lines.append(f"\n--- IA ANÁLISIS ---")
            lines.append(f"  Mercados analizados que dieron SKIP: {len(skips)}")
            # Mostrar últimos 5 skips
            for s in skips[-5:]:
                hora = s[:8] if len(s) > 8 else ""
                # Extraer nombre del mercado
                if "⏭️" in s:
                    market = s.split("⏭️")[-1].split(":")[0].strip()
                    lines.append(f"    {hora} SKIP: {market[:50]}")

            # === ERRORES ===
            errors = [l for l in log_lines if "ERROR" in l.upper() and "polybot" in l.lower()]
            error_count = len(errors)
            lines.append(f"\n--- ERRORES ---")
            if error_count == 0:
                lines.append(f"  ✅ Sin errores durante el día")
            else:
                lines.append(f"  ⚠️ {error_count} errores detectados:")
                for e in errors[-10:]:
                    hora = e[:8] if len(e) > 8 else ""
                    msg = e.split("]")[-1].strip()[:80] if "]" in e else e[:80]
                    lines.append(f"    {hora} {msg}")

            # === AUTO-SELL ===
            sells = [l for l in log_lines if "VENDIDO" in l or "VENTA" in l]
            sell_attempts = [l for l in log_lines if "STOP LOSS" in l or "TAKE PROFIT" in l]
            lines.append(f"\n--- AUTO-SELL ---")
            if not sells and not sell_attempts:
                lines.append(f"  Sin actividad de auto-sell")
            else:
                lines.append(f"  Intentos de venta: {len(sell_attempts)}")
                lines.append(f"  Ventas exitosas: {len(sells)}")
                for s in sells[-5:]:
                    hora = s[:8] if len(s) > 8 else ""
                    msg = s.split("]")[-1].strip()[:70] if "]" in s else s[:70]
                    lines.append(f"    {hora} {msg}")

            # === AUTO-REDEEM ===
            redeems = [l for l in log_lines if "Cobrado" in l or "COBRO" in l]
            redeem_attempts = [l for l in log_lines if "AUTO-COBRO" in l]
            lines.append(f"\n--- AUTO-REDEEM ---")
            lines.append(f"  Intentos de cobro: {len(redeem_attempts)}")
            if redeems:
                for r in redeems[-5:]:
                    hora = r[:8] if len(r) > 8 else ""
                    msg = r.split("]")[-1].strip()[:70] if "]" in r else r[:70]
                    lines.append(f"    {hora} {msg}")
            else:
                lines.append(f"  Sin cobros exitosos hoy")

            # === KILL SWITCH / PAUSAS ===
            kills = [l for l in log_lines if "KILL SWITCH" in l or "pérdidas seguidas" in l or "PAUSADO" in l]
            lines.append(f"\n--- PROTECCIONES ---")
            if not kills:
                lines.append(f"  ✅ Sin activaciones de kill switch o pausas")
            else:
                for k in kills:
                    hora = k[:8] if len(k) > 8 else ""
                    msg = k.split("]")[-1].strip()[:70] if "]" in k else k[:70]
                    lines.append(f"    ⚠️ {hora} {msg}")

            # === ESTRATEGIAS SIN OPORTUNIDADES ===
            no_crypto = log_content.count("No hay mercados crypto cortos activos")
            no_harvest = log_content.count("Sin oportunidades de harvest")
            no_weather = log_content.count("Sin oportunidades de clima")
            no_stocks = log_content.count("No se encontraron mercados de bolsa")
            no_esports = log_content.count("No hay mercados de esports")

            lines.append(f"\n--- OPORTUNIDADES POR ESTRATEGIA ---")
            lines.append(f"  Crypto Grinder: sin mercados {no_crypto}/{cycle_count} ciclos")
            lines.append(f"  Harvester: sin oportunidades {no_harvest}/{cycle_count} ciclos")
            lines.append(f"  Weather: sin oportunidades {no_weather}/{cycle_count} ciclos")
            lines.append(f"  Stocks: sin mercados {no_stocks}/{cycle_count} ciclos")
            lines.append(f"  Esports: sin mercados {no_esports}/{cycle_count} ciclos")

        except Exception as e:
            lines.append(f"  Error leyendo log: {e}")

    lines.append(f"\n{'=' * 60}")
    lines.append(f"  FIN DEL REPORTE")
    lines.append(f"{'=' * 60}")

    report_text = "\n".join(lines)

    # Guardar en archivo
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(REPORT_DIR, f"report_{now.strftime('%Y%m%d_%H%M')}.txt")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_text)

    # También guardar como "latest"
    latest_file = os.path.join(REPORT_DIR, "latest_report.txt")
    with open(latest_file, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(report_text)
    print(f"\n📁 Guardado en: {report_file}")
    print(f"📁 También en: {latest_file}")

    return report_text


if __name__ == "__main__":
    asyncio.run(generate_report())
