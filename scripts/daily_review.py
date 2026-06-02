#!/usr/bin/env python3
"""
PolyBot — Daily Review automático (a Telegram)
==============================================
Resumen diario que se envía SOLO a tu Telegram: balance, WR por estrategia,
trades de hoy, estado de flags y alertas si algo se ve mal. Pensado para correr
por cron 1-2 veces al día. Si algo no luce bien (balance bajando, racha de
pérdidas, bot caído) lo marca con ⚠️ para que lo revises sin abrir el servidor.

USO (cron):
    0 21 * * *  /root/Polybot/venv/bin/python /root/Polybot/scripts/daily_review.py
"""

import os, sys, json, asyncio, subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def _load_trades():
    p = Path("data/trade_results.json")
    if not p.exists():
        return []
    try:
        return json.load(open(p))
    except Exception:
        return []


def _balance():
    try:
        from web3 import Web3
        from dotenv import load_dotenv
        load_dotenv()
        w3 = Web3(Web3.HTTPProvider(os.getenv("ALCHEMY_RPC_URL", "https://polygon-bor-rpc.publicnode.com")))
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS")
        pusd = w3.eth.contract(
            address=w3.to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"),
            abi=[{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
                  "outputs": [{"name": "", "type": "uint256"}], "type": "function"}])
        return pusd.functions.balanceOf(w3.to_checksum_address(funder)).call() / 1e6
    except Exception:
        return None


def _service_active():
    try:
        return subprocess.run(["systemctl", "is-active", "polybot"],
                              capture_output=True, text=True).stdout.strip() == "active"
    except Exception:
        return None


def build_report():
    trades = _load_trades()
    bal = _balance()
    active = _service_active()
    alerts = []

    # WR por estrategia (resueltos)
    agg = defaultdict(lambda: [0, 0, 0.0])  # res, won, pnl
    for t in trades:
        if t.get("result") in ("WON", "LOST"):
            s = t.get("strategy", "?")
            agg[s][0] += 1
            if t["result"] == "WON":
                agg[s][1] += 1
            agg[s][2] += float(t.get("profit", 0) or 0)

    # Trades de las últimas 24h
    cutoff = datetime.now() - timedelta(hours=24)
    today = []
    for t in trades:
        try:
            ts = datetime.fromisoformat(t.get("timestamp", "")[:19])
            if ts >= cutoff:
                today.append(t)
        except Exception:
            pass

    # Flags activos
    try:
        from config.settings import SAFETY
        flags = (f"📈{'ON' if getattr(SAFETY,'enable_stock_trader',True) else 'OFF'} "
                 f"⚽{'ON' if getattr(SAFETY,'enable_football_trader',True) else 'OFF'} "
                 f"🏛️{'ON' if getattr(SAFETY,'enable_politics_trader',True) else 'OFF'}")
    except Exception:
        flags = "?"

    # ----- Alertas automáticas -----
    if active is False:
        alerts.append("🔴 El servicio polybot NO está activo")
    if bal is not None and bal < 45:
        alerts.append(f"🔴 Balance bajo: ${bal:.2f} (cerca del kill-switch)")
    # Racha de pérdidas recientes (últimos 5 resueltos)
    resolved = [t for t in trades if t.get("result") in ("WON", "LOST")][-5:]
    if len(resolved) == 5 and all(t["result"] == "LOST" for t in resolved):
        alerts.append("🔴 5 pérdidas seguidas en los últimos trades")
    if not today:
        alerts.append("🟡 0 apuestas en 24h (¿poca señal o filtro de más?)")

    # ----- Construir mensaje -----
    L = []
    L.append(f"📊 *POLYBOT — Review {datetime.now().strftime('%d-%b %H:%M')}*")
    bal_s = f"${bal:.2f}" if bal is not None else "n/d"
    svc_s = "🟢 activo" if active else ("🔴 caído" if active is False else "n/d")
    L.append(f"💰 Balance: *{bal_s}*  |  Bot: {svc_s}")
    L.append(f"Estrategias: {flags}")
    L.append("")
    L.append("*Win-rate (histórico resuelto):*")
    for s, (res, won, pnl) in sorted(agg.items()):
        wr = f"{won/res*100:.0f}%" if res else "—"
        sign = "🟢" if pnl >= 0 else "🔴"
        L.append(f"  {s}: {won}/{res} ({wr})  {sign}${pnl:+.2f}")
    L.append("")
    L.append(f"*Últimas 24h:* {len(today)} apuesta(s)")
    for t in today[-6:]:
        r = t.get("result", "PEND")
        ic = "✅" if r == "WON" else ("❌" if r == "LOST" else "⏳")
        L.append(f"  {ic} {t.get('strategy','?')} ${t.get('amount','?')} — {t.get('question','')[:34]}")

    if alerts:
        L.append("")
        L.append("*⚠️ Atención:*")
        for a in alerts:
            L.append(f"  {a}")
    else:
        L.append("")
        L.append("✅ Todo en orden.")

    return "\n".join(L)


async def main():
    report = build_report()
    print(report)
    try:
        from modules.telegram_monitor import TelegramMonitor
        tg = TelegramMonitor()
        if tg.enabled:
            await tg.send(report, parse_mode="Markdown")
            await tg.close()
            print("\n✅ Enviado a Telegram")
        else:
            print("\n⚠️ Telegram no configurado (.env)")
    except Exception as e:
        print(f"\n⚠️ No se pudo enviar a Telegram: {e}")


if __name__ == "__main__":
    asyncio.run(main())
