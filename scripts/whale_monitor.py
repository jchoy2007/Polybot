#!/usr/bin/env python3
"""
Monitor top Polymarket wallets (READ-ONLY).
Loggea posiciones activas de whales para análisis de patrones.
Detecta posiciones NUEVAS o cambios >$500 vs snapshot anterior
y alerta por Telegram si está configurado.
NO ejecuta trades.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp
from datetime import datetime

WHALES = {
    # Top whales descubiertos por heurística portfolio-size (22-Abr-2026):
    # cruce de wallets con trades ≥ $10k en las últimas 500 entradas del
    # endpoint /trades contra el tamaño de portfolio actual vía /positions.
    # NO es ranking por PnL (requiere histórico) — es proxy por capital
    # desplegado. Reseleccionar cada 4-6 semanas.
    "coinman2":       "0x55be7aa03ecfbe37aa5460db791205f7ac9ddca3",  # ~$58k (user-provided)
    "Lost-Macadamia": "0x45b39e1f71e47fd4afe4b988ffad690b644735bc",  # ~$1.6M portfolio
    "Neat-Spine":     "0x36a3f17401e395ef4cb1b7f42bcdb8ab8e15fafb",  # ~$1.3M portfolio, muy activo
}

DATA_API = "https://data-api.polymarket.com"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = PROJECT_ROOT / "data" / "whale_snapshots.json"
DELTA_THRESHOLD_USD = 500.0


async def get_whale_positions(session: aiohttp.ClientSession, wallet: str):
    url = f"{DATA_API}/positions?user={wallet.lower()}"
    try:
        async with session.get(url, timeout=15) as r:
            if r.status == 200:
                return await r.json()
            print(f"      ⚠️ HTTP {r.status} para {wallet[:10]}...")
    except Exception as e:
        print(f"      ⚠️ Error: {e}")
    return []


def _position_key(p: dict) -> str:
    """Clave única por posición: conditionId+outcome, con fallbacks."""
    cid = p.get("conditionId") or p.get("asset") or p.get("slug") or ""
    outcome = p.get("outcome") or p.get("outcomeIndex") or ""
    return f"{cid}_{outcome}"


def _load_snapshot() -> dict:
    if not SNAPSHOT_PATH.exists():
        return {"timestamp": "", "wallets": {}}
    try:
        with open(SNAPSHOT_PATH) as f:
            return json.load(f)
    except Exception as e:
        print(f"   ⚠️ snapshot corrupto ({e}), se reinicializa")
        return {"timestamp": "", "wallets": {}}


def _save_snapshot(snapshot: dict):
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w") as f:
        json.dump(snapshot, f, indent=2)


async def _maybe_telegram_alert(lines: list):
    """Envía alertas por Telegram si está configurado. Fail-silent."""
    if not lines:
        return
    if not (os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")):
        return
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from modules.telegram_monitor import TelegramMonitor
        tg = TelegramMonitor()
        if not tg.enabled:
            return
        header = f"🐋 Whale activity ({len(lines)} cambio/s)"
        body = "\n".join(lines[:20])
        await tg.send(f"{header}\n\n{body}")
        await tg.close()
    except Exception as e:
        print(f"      ⚠️ Telegram alert falló: {e}")


async def main():
    print(f"🐋 WHALE MONITOR — {datetime.utcnow().isoformat()}Z")
    print("=" * 70)

    prev_snapshot = _load_snapshot()
    prev_wallets = prev_snapshot.get("wallets", {})
    new_wallets: dict = {}
    alerts: list = []

    async with aiohttp.ClientSession() as session:
        for name, wallet in WHALES.items():
            positions = await get_whale_positions(session, wallet)

            active = []
            current: dict = {}
            for p in positions:
                size = float(p.get("size") or 0)
                cur_price = float(p.get("curPrice") or 0)
                val = size * cur_price
                if val > 1.0:
                    p["_value"] = val
                    active.append(p)
                    key = _position_key(p)
                    current[key] = {
                        "size": size,
                        "side": p.get("outcome", "?"),
                        "price": cur_price,
                        "value": val,
                        "title": (p.get("title") or "?")[:80],
                    }
            new_wallets[name] = current

            total_value = sum(p["_value"] for p in active)
            print(f"\n📊 {name} ({wallet[:10]}...{wallet[-4:]})")
            print(f"   Posiciones activas (>$1): {len(active)}")
            print(f"   Valor total: ${total_value:,.2f}")

            top5 = sorted(active, key=lambda x: x["_value"], reverse=True)[:5]
            for p in top5:
                title = (p.get("title") or "?")[:55]
                outcome = p.get("outcome", "?")
                val = p["_value"]
                pct = float(p.get("curPrice") or 0) * 100
                print(f"      • {title} | {outcome} @ ${val:.2f} ({pct:.0f}%)")

            # Delta vs snapshot anterior
            prev = prev_wallets.get(name, {})
            for key, cur in current.items():
                prev_pos = prev.get(key)
                if prev_pos is None:
                    # Posición NUEVA
                    if cur["value"] >= DELTA_THRESHOLD_USD:
                        msg = (
                            f"🆕 {name} compró {cur['side']} en "
                            f"{cur['title']} (${cur['value']:,.0f})"
                        )
                        print(f"   {msg}")
                        alerts.append(msg)
                else:
                    diff_val = cur["value"] - float(prev_pos.get("value", 0))
                    if abs(diff_val) >= DELTA_THRESHOLD_USD:
                        arrow = "➕" if diff_val > 0 else "➖"
                        msg = (
                            f"{arrow} {name} Δ {cur['side']} en "
                            f"{cur['title']} (${diff_val:+,.0f} → "
                            f"${cur['value']:,.0f})"
                        )
                        print(f"   {msg}")
                        alerts.append(msg)

    snapshot = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "wallets": new_wallets,
    }
    _save_snapshot(snapshot)

    if alerts:
        print(f"\n🚨 {len(alerts)} cambios detectados")
        await _maybe_telegram_alert(alerts)
    else:
        print("\n✅ Sin cambios relevantes (>$500)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"⚠️ whale_monitor fatal: {e}")
        # Exit 0 para no spamear cron con alertas si la API está flaky.
