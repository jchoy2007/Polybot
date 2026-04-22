#!/usr/bin/env python3
"""
Monitor top Polymarket wallets (READ-ONLY).
Loggea posiciones activas de whales para análisis de patrones.
NO ejecuta trades.
"""
import asyncio
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


async def main():
    print(f"🐋 WHALE MONITOR — {datetime.utcnow().isoformat()}Z")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        for name, wallet in WHALES.items():
            positions = await get_whale_positions(session, wallet)

            active = []
            for p in positions:
                size = float(p.get("size") or 0)
                cur_price = float(p.get("curPrice") or 0)
                val = size * cur_price
                if val > 1.0:
                    p["_value"] = val
                    active.append(p)

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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"⚠️ whale_monitor fatal: {e}")
        # Exit 0 para no spamear cron con alertas si la API está flaky.
