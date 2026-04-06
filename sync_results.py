# -*- coding: utf-8 -*-
"""
PolyBot - Sincronizar Resultados (v3 - Historia Completa)
==========================================================
Reconstruye TODA la historia de trades desde bets_placed.json
y actualiza trade_results.json con resultados reales.

USO: python sync_results.py
"""

import os, sys, json, asyncio, aiohttp
from datetime import datetime
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = "data"
RESULTS_FILE = f"{DATA_DIR}/trade_results.json"
HISTORY_FILE = f"{DATA_DIR}/bot_history.json"
BETS_FILE = f"{DATA_DIR}/bets_placed.json"


def load_json(path):
    try:
        with open(path) as f: return json.load(f)
    except: return None


def save_json(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f: json.dump(data, f, indent=2)


async def get_positions():
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not pk: return []
    from web3 import Web3
    address = Web3().eth.account.from_key(pk).address
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    addrs = [a.lower() for a in [funder, address] if a]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for addr in addrs:
            try:
                async with session.get(f"https://data-api.polymarket.com/positions?user={addr}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list) and len(data) > 0:
                            return data
            except: continue
    return []


async def get_balance():
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not pk: return 0
    try:
        from web3 import Web3
        for rpc in ["https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic"]:
            try:
                w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
                if w3.is_connected():
                    account = w3.eth.account.from_key(pk)
                    USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
                    abi = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf",
                            "outputs":[{"name":"","type":"uint256"}],"type":"function"}]
                    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E), abi=abi)
                    return usdc.functions.balanceOf(account.address).call() / 1e6
            except: continue
    except: pass
    return 0


# Trades conocidos del Dia 1-2 (antes del tracker) con resultados confirmados
KNOWN_HISTORICAL = [
    # Dia 1 - cobrados exitosamente
    {"question": "Ethereum dip to $2,000 on April 2", "side": "NO", "amount": 5.0, "price": 0.90,
     "strategy": "HARVEST", "result": "WON", "timestamp": "2026-04-02T14:30:00"},
    {"question": "Bitcoin Up or Down - April 3, 1:25AM", "side": "DOWN", "amount": 4.0, "price": 0.50,
     "strategy": "CRYPTO", "result": "WON", "timestamp": "2026-04-03T05:12:00"},
    {"question": "Bucharest Open: Dzumhur vs Zandschulp", "side": "YES", "amount": 5.0, "price": 0.55,
     "strategy": "IA", "result": "WON", "timestamp": "2026-04-03T05:16:00"},
    {"question": "Ethereum Up or Down - April 3, 6:25AM", "side": "UP", "amount": 4.0, "price": 0.50,
     "strategy": "CRYPTO", "result": "WON", "timestamp": "2026-04-03T05:28:00"},
    {"question": "Dota 2: PARIVISION vs Natus Vincere", "side": "YES", "amount": 5.0, "price": 0.60,
     "strategy": "IA", "result": "WON", "timestamp": "2026-04-03T05:42:00"},
    {"question": "Solana Up or Down - April 3, 7:25AM", "side": "DOWN", "amount": 4.0, "price": 0.50,
     "strategy": "CRYPTO", "result": "WON", "timestamp": "2026-04-03T05:42:13"},
    {"question": "Bulls vs. Knicks", "side": "NO", "amount": 6.59, "price": 0.95,
     "strategy": "HARVEST", "result": "WON", "timestamp": "2026-04-03T07:46:00"},
    {"question": "Ethereum above $2,100 on April 3", "side": "YES", "amount": 5.0, "price": 0.92,
     "strategy": "HARVEST", "result": "WON", "timestamp": "2026-04-03T07:59:00"},
    {"question": "Counter-Strike: K27 vs 100 Thieves", "side": "YES", "amount": 5.0, "price": 0.90,
     "strategy": "HARVEST", "result": "WON", "timestamp": "2026-04-03T08:13:00"},
    {"question": "Ethereum Up or Down - April 3, 10:45AM", "side": "UP", "amount": 4.0, "price": 0.50,
     "strategy": "CRYPTO", "result": "WON", "timestamp": "2026-04-03T09:29:00"},
    {"question": "Solana Up or Down - April 3, 11:25AM", "side": "DOWN", "amount": 4.0, "price": 0.50,
     "strategy": "CRYPTO", "result": "WON", "timestamp": "2026-04-03T09:43:00"},
    {"question": "Solana Up or Down - April 3, 11AM ET", "side": "UP", "amount": 4.0, "price": 0.50,
     "strategy": "CRYPTO", "result": "WON", "timestamp": "2026-04-03T10:29:00"},
    # Trades perdidos por bugs (Dia 3)
    {"question": "BNB Up or Down - April 4 (bug stocks)", "side": "DOWN", "amount": 5.03, "price": 0.50,
     "strategy": "STOCKS", "result": "LOST", "timestamp": "2026-04-04T00:45:51"},
]


async def sync():
    print("=" * 50)
    print("  SYNC RESULTADOS - Historia Completa")
    print("=" * 50)

    trades = load_json(RESULTS_FILE) or []
    bets = load_json(BETS_FILE) or {"market_ids": [], "history": []}
    positions = await get_positions()
    balance = await get_balance()

    print(f"  Trades en registro: {len(trades)}")
    print(f"  Bets historicas: {len(bets.get('history', []))}")
    print(f"  Posiciones activas: {len(positions)}")
    print(f"  Balance: ${balance:.2f}")
    print()

    # Paso 1: Agregar trades historicos que no estan en trade_results.json
    existing_questions = set()
    for t in trades:
        q = t.get("question", "").lower()[:20]
        existing_questions.add(q)

    added = 0
    for ht in KNOWN_HISTORICAL:
        q_check = ht["question"].lower()[:20]
        if q_check not in existing_questions:
            payout = 0
            profit = 0
            if ht["result"] == "WON":
                payout = round(ht["amount"] / ht["price"], 2) if ht["price"] > 0 else ht["amount"]
                profit = round(payout - ht["amount"], 2)
            elif ht["result"] == "LOST":
                profit = round(-ht["amount"], 2)

            trades.append({
                "market_id": "",
                "question": ht["question"][:60],
                "side": ht["side"],
                "amount": ht["amount"],
                "price": ht["price"],
                "strategy": ht["strategy"],
                "timestamp": ht["timestamp"],
                "result": ht["result"],
                "payout": payout,
                "profit": profit,
            })
            existing_questions.add(q_check)
            added += 1
            tag = "[WIN]" if ht["result"] == "WON" else "[LOSS]"
            print(f"  + {tag} {ht['question'][:45]}")

    if added > 0:
        print(f"  >> {added} trades historicos agregados")
    print()

    # Paso 2: Actualizar trades PENDING con posiciones actuales
    pos_lookup = {}
    for p in positions:
        title = (p.get("title") or p.get("question") or "").lower()
        pos_lookup[title] = p
        cid = str(p.get("conditionId") or "")
        if cid: pos_lookup[cid] = p

    updated = 0
    for trade in trades:
        if trade.get("result") != "PENDING":
            continue

        question = trade.get("question", "").lower()
        market_id = trade.get("market_id", "")

        matched = pos_lookup.get(market_id)
        if not matched:
            for pt, p in pos_lookup.items():
                if len(question) > 15 and question[:15] in pt:
                    matched = p
                    break
                if len(pt) > 15 and pt[:15] in question:
                    matched = p
                    break

        if matched:
            cur_price = float(matched.get("curPrice", 0) or 0)
            value = float(matched.get("currentValue", 0) or 0)
            size = float(matched.get("size", 0) or 0)

            if cur_price >= 0.99 or (value > 0 and size > 0 and value >= size * 0.95):
                trade["result"] = "WON"
                trade["payout"] = round(value if value > 0 else size, 2)
                trade["profit"] = round(trade["payout"] - trade.get("amount", 0), 2)
                updated += 1
                print(f"  [WIN] {trade['question'][:45]} | +${trade['profit']:.2f}")

            elif cur_price <= 0.01 and value <= 0.01:
                trade["result"] = "LOST"
                trade["payout"] = 0
                trade["profit"] = round(-trade.get("amount", 0), 2)
                updated += 1
                print(f"  [LOSS] {trade['question'][:45]} | -${trade.get('amount', 0):.2f}")
        else:
            # No en posiciones = probablemente cobrado
            if market_id:
                trade["result"] = "WON"
                amount = trade.get("amount", 0)
                price = trade.get("price", 0.5)
                trade["payout"] = round(amount / price, 2) if price > 0 else amount
                trade["profit"] = round(trade["payout"] - amount, 2)
                updated += 1
                print(f"  [WIN cobrado] {trade['question'][:40]} | +${trade['profit']:.2f}")

    if updated > 0:
        print(f"  >> {updated} trades actualizados")

    # Guardar
    save_json(RESULTS_FILE, trades)

    # Resumen final
    won = [t for t in trades if t.get("result") == "WON"]
    lost = [t for t in trades if t.get("result") == "LOST"]
    pending = [t for t in trades if t.get("result") == "PENDING"]
    total = len(won) + len(lost)
    wr = len(won) / total * 100 if total > 0 else 0
    profit = sum(t.get("profit", 0) for t in won)
    loss = sum(t.get("profit", 0) for t in lost)
    pos_value = sum(float(p.get("currentValue", 0) or 0)
                    for p in positions if float(p.get("currentValue", 0) or 0) > 0.01)

    print(f"\n{'='*50}")
    print(f"  RESUMEN TOTAL (desde 2 de abril)")
    print(f"{'='*50}")
    print(f"  Total trades: {len(trades)}")
    print(f"  Win Rate: {len(won)}/{total} ({wr:.0f}%)")
    print(f"  Ganado: +${profit:.2f}")
    print(f"  Perdido: ${loss:.2f}")
    print(f"  Neto: ${profit + loss:+.2f}")
    print(f"  Pendientes: {len(pending)}")
    print(f"  Balance libre: ${balance:.2f}")
    print(f"  En posiciones: ${pos_value:.2f}")
    print(f"  Total estimado: ${balance + pos_value:.2f}")

    # Por estrategia
    strats = {}
    for t in trades:
        s = t.get("strategy", "IA")
        if s not in strats: strats[s] = {"w": 0, "l": 0, "p": 0, "profit": 0}
        if t["result"] == "WON": strats[s]["w"] += 1; strats[s]["profit"] += t.get("profit", 0)
        elif t["result"] == "LOST": strats[s]["l"] += 1; strats[s]["profit"] += t.get("profit", 0)
        else: strats[s]["p"] += 1

    print(f"\n  POR ESTRATEGIA:")
    for name, s in strats.items():
        st = s["w"] + s["l"]
        swr = s["w"] / st * 100 if st > 0 else 0
        print(f"    {name}: {s['w']}W/{s['l']}L/{s['p']}P ({swr:.0f}%) | ${s['profit']:+.2f}")

    # Guardar historial
    history = load_json(HISTORY_FILE) or {}
    history["first_start"] = "2026-04-02T14:00:00"
    history["last_sync"] = datetime.now().isoformat()
    history["stats"] = {
        "total_trades": len(trades), "won": len(won), "lost": len(lost),
        "pending": len(pending), "win_rate": round(wr, 1),
        "total_profit": round(profit, 2), "total_loss": round(loss, 2),
        "net_pnl": round(profit + loss, 2), "balance": round(balance, 2),
        "positions_value": round(pos_value, 2),
        "total_estimated": round(balance + pos_value, 2),
    }
    history["strategies"] = {k: dict(v) for k, v in strats.items()}
    save_json(HISTORY_FILE, history)
    print(f"\n  Historial guardado")


if __name__ == "__main__":
    asyncio.run(sync())
