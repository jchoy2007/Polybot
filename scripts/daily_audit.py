#!/usr/bin/env python3
"""
Daily audit — snapshot rápido del estado del bot.

Uso:
    cd /root/Polybot && ./venv/bin/python scripts/daily_audit.py

Imprime:
- Fecha/hora, balance USDC.e, win rate, P&L por estrategia
- Trades de las últimas 24h
- Posiciones abiertas con valor >= $5 y tiempo a resolución
"""
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TRADE_FILE = ROOT / "data" / "trade_results.json"
USDC_ADDR = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
GAMMA_URL = "https://gamma-api.polymarket.com/markets"


def get_usdc_balance() -> float:
    from web3 import Web3
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    rpc = os.getenv("ALCHEMY_RPC_URL") or "https://polygon-bor-rpc.publicnode.com"
    w3 = Web3(Web3.HTTPProvider(rpc))
    addr = w3.eth.account.from_key(os.getenv("POLYGON_WALLET_PRIVATE_KEY")).address
    abi = [{"inputs":[{"name":"a","type":"address"}],"name":"balanceOf",
            "outputs":[{"name":"","type":"uint256"}],"type":"function"}]
    c = w3.eth.contract(address=w3.to_checksum_address(USDC_ADDR), abi=abi)
    return c.functions.balanceOf(addr).call() / 1e6


async def fetch_market(session: aiohttp.ClientSession, mid: str) -> dict | None:
    try:
        async with session.get(f"{GAMMA_URL}?id={mid}", timeout=10) as r:
            if r.status != 200:
                return None
            j = await r.json()
            return j[0] if j else None
    except Exception:
        return None


async def fetch_all(mids: list[str]) -> dict[str, dict]:
    async with aiohttp.ClientSession() as s:
        results = await asyncio.gather(*[fetch_market(s, m) for m in mids])
    return {m: r for m, r in zip(mids, results) if r}


def current_value(trade: dict, market: dict | None) -> tuple[float, float]:
    """Return (current_value_usd, current_price_of_bought_side)."""
    if not market:
        return (0.0, 0.0)
    try:
        outcomes = json.loads(market.get("outcomes", "[]"))
        prices = json.loads(market.get("outcomePrices", "[]"))
        side = (trade.get("side") or "").upper()
        idx = 0 if side == "YES" else 1
        if idx >= len(prices):
            return (0.0, 0.0)
        cur_price = float(prices[idx])
        entry = float(trade["price"])
        shares = trade["amount"] / entry if entry > 0 else 0
        return (shares * cur_price, cur_price)
    except Exception:
        return (0.0, 0.0)


def fmt_duration(seconds: float) -> str:
    if seconds < 0:
        return "RESOLVIDO"
    h = seconds / 3600
    if h < 24:
        return f"{h:.1f}h"
    return f"{h/24:.1f}d"


async def main() -> None:
    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"  POLYBOT DAILY AUDIT — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    # 1. Balance
    try:
        bal = get_usdc_balance()
        print(f"\n💰 Balance USDC.e líquido:  ${bal:.2f}")
    except Exception as e:
        print(f"\n⚠️  No se pudo leer balance: {e}")
        bal = 0.0

    # 2. Trades
    with open(TRADE_FILE) as f:
        trades = json.load(f)
    resolved = [t for t in trades if t.get("result") in ("WON", "LOST")]
    pending = [t for t in trades if t.get("result") == "PENDING"]

    # Win rate totales
    wins = [t for t in resolved if t["result"] == "WON"]
    pnl = sum(t.get("profit", 0) for t in resolved)
    print(f"\n📊 WIN RATE TOTAL: {len(wins)}/{len(resolved)} "
          f"({len(wins)/max(len(resolved),1)*100:.0f}%) | Neto: ${pnl:+.2f}")

    # Por estrategia
    print("\n📊 POR ESTRATEGIA:")
    for strat in ("SPORTS", "STOCKS", "CRYPTO"):
        sr = [t for t in resolved if t.get("strategy") == strat]
        sp = [t for t in pending if t.get("strategy") == strat]
        if not sr and not sp:
            continue
        sw = sum(1 for t in sr if t["result"] == "WON")
        spnl = sum(t.get("profit", 0) for t in sr)
        wr = f"{sw}/{len(sr)} ({sw/max(len(sr),1)*100:.0f}%)" if sr else "0/0"
        print(f"   {strat:7} | {wr:<12} | P&L ${spnl:+7.2f} | Pendientes: {len(sp)}")

    # 3. Trades últimas 24h
    cutoff = now - timedelta(hours=24)
    recent = [t for t in trades
              if datetime.fromisoformat(t["timestamp"]).replace(tzinfo=timezone.utc) >= cutoff]
    print(f"\n🕐 TRADES ÚLTIMAS 24h: {len(recent)}")
    for t in sorted(recent, key=lambda x: x["timestamp"]):
        ts = datetime.fromisoformat(t["timestamp"])
        r = t.get("result", "PENDING")
        tag = "✅" if r == "WON" else ("❌" if r == "LOST" else "⏳")
        profit = f" ${t.get('profit',0):+.2f}" if r != "PENDING" else ""
        print(f"   {tag} {ts.strftime('%H:%M')} | {t.get('strategy',''):7} | "
              f"${t.get('amount'):5.2f} @ {t.get('price'):.3f}{profit} | "
              f"{t.get('question','')[:50]}")

    # 4. Posiciones abiertas con valor actual
    print(f"\n🏦 POSICIONES ABIERTAS ({len(pending)}):")
    mids = list({t["market_id"] for t in pending})
    markets = await fetch_all(mids)
    rows = []
    total_cost = 0.0
    total_value = 0.0
    for t in pending:
        m = markets.get(t["market_id"])
        val, cur_p = current_value(t, m)
        end = m.get("endDate") if m else None
        secs_left = -1
        if end:
            try:
                endt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                secs_left = (endt - now).total_seconds()
            except Exception:
                pass
        rows.append((t, val, cur_p, secs_left))
        total_cost += t["amount"]
        total_value += val

    # ordenar por tiempo a resolución (asc)
    rows.sort(key=lambda x: (x[3] if x[3] >= 0 else 9e9))
    print(f"   {'Resuelve':>9} {'Strat':<7} {'Costo':>6} {'Valor':>6} "
          f"{'P&L∆':>7} {'Entry→Now':>11}  Mercado")
    for t, val, cur_p, secs in rows:
        delta = val - t["amount"]
        flag = ""
        if val >= 5.0:
            flag = " ⭐"
        elif val <= 0.10 * t["amount"]:
            flag = " ⚠️"
        print(f"   {fmt_duration(secs):>9} {t.get('strategy',''):<7} "
              f"${t['amount']:5.2f} ${val:5.2f} ${delta:+6.2f} "
              f"{t['price']:.3f}→{cur_p:.3f}  "
              f"{t.get('question','')[:45]}{flag}")

    print(f"\n   TOTAL: invertido ${total_cost:.2f} → valor actual ${total_value:.2f} "
          f"(P&L no realizado ${total_value - total_cost:+.2f})")
    print(f"   BANKROLL TOTAL (líquido + posiciones): ${bal + total_value:.2f}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
