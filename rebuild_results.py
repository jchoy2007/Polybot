"""
PolyBot - Rebuild Trade Results
=================================
Reconstruye trade_results.json desde el historial 
completo de Polymarket (ganadas + perdidas).
"""

import sys, json, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from urllib.request import urlopen, Request
from dotenv import load_dotenv
load_dotenv()

RESULTS_FILE = "data/trade_results.json"
DATA_API = "https://data-api.polymarket.com"

def main():
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    
    if not pk:
        print("Error: No hay private key")
        return
    
    from web3 import Web3
    addr = Web3().eth.account.from_key(pk).address
    
    print(f"\n{'='*60}")
    print(f"  Rebuild Trade Results - Historia Completa")
    print(f"{'='*60}\n")
    
    # Obtener TODAS las posiciones (activas + resueltas)
    all_positions = []
    for a in [funder, addr]:
        if not a:
            continue
        try:
            # Posiciones activas
            req = Request(f"{DATA_API}/positions?user={a.lower()}", headers={"User-Agent": "PolyBot"})
            with urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                if data and isinstance(data, list):
                    for p in data:
                        p["_source"] = "active"
                    all_positions.extend(data)
                    print(f"  Posiciones activas: {len(data)}")
                    break
        except Exception as e:
            print(f"  Error: {e}")
    
    # Intentar obtener historial de trades
    for a in [funder, addr]:
        if not a:
            continue
        try:
            req = Request(f"{DATA_API}/trades?user={a.lower()}&limit=200", headers={"User-Agent": "PolyBot"})
            with urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                if data and isinstance(data, list):
                    print(f"  Trades historicos: {len(data)}")
        except:
            pass
    
    # Cargar trade_results existente
    existing = []
    try:
        with open(RESULTS_FILE) as f:
            existing = json.load(f)
    except:
        pass
    
    print(f"  Trades en archivo: {len(existing)}")
    
    # Crear mapa de trades existentes por question
    existing_map = {}
    for t in existing:
        key = t.get("question", "")[:25].lower()
        existing_map[key] = t
    
    # Procesar cada posicion
    trades = list(existing)  # Mantener los existentes
    
    for pos in all_positions:
        title = pos.get("title") or pos.get("question") or ""
        outcome = pos.get("outcome") or ""
        size = float(pos.get("size") or 0)
        cur_value = float(pos.get("currentValue") or 0)
        cur_price = float(pos.get("curPrice") or 0)
        initial_value = float(pos.get("initialValue") or pos.get("cashPaid") or 0)
        avg_price = float(pos.get("avgPrice") or 0)
        pnl = float(pos.get("cashPnl") or 0)
        
        if size <= 0 and cur_value <= 0:
            continue
        
        # Ver si ya existe
        key = title[:25].lower()
        if key in existing_map:
            # Actualizar resultado si todavia PENDING
            t = existing_map[key]
            if t.get("result") == "PENDING":
                if cur_value <= 0.01 and cur_price <= 0.05:
                    t["result"] = "LOST"
                    t["payout"] = 0
                    t["profit"] = -t["amount"]
                elif cur_price >= 0.95:
                    t["result"] = "WON"
                    tokens = t["amount"] / t["price"] if t["price"] > 0 else 0
                    t["payout"] = round(tokens, 2)
                    t["profit"] = round(tokens - t["amount"], 2)
            continue
        
        # Determinar estrategia
        tl = title.lower()
        if "up or down" in tl or "up/down" in tl:
            strategy = "CRYPTO"
        elif "temperature" in tl or "weather" in tl:
            strategy = "WEATHER"
        elif "s&p" in tl or "nasdaq" in tl or "dow" in tl:
            strategy = "STOCKS"
        else:
            strategy = "IA"
        
        # Determinar resultado
        result = "PENDING"
        profit = 0
        payout = 0
        
        if cur_price >= 0.95 and cur_value > 0:
            result = "WON"
            payout = cur_value
            profit = cur_value - initial_value if initial_value > 0 else pnl
        elif cur_price <= 0.05 or cur_value <= 0.01:
            result = "LOST"
            payout = 0
            profit = -initial_value if initial_value > 0 else pnl
        
        amount = initial_value if initial_value > 0 else (cur_value - pnl if pnl else cur_value)
        
        trade = {
            "market_id": pos.get("conditionId", ""),
            "question": title[:60],
            "side": outcome,
            "amount": round(abs(amount), 2),
            "price": round(avg_price, 3) if avg_price > 0 else 0.5,
            "strategy": strategy,
            "timestamp": pos.get("createdAt") or pos.get("timestamp") or "2026-04-02",
            "result": result,
            "payout": round(payout, 2),
            "profit": round(profit, 2),
        }
        
        trades.append(trade)
    
    # Guardar
    os.makedirs("data", exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(trades, f, indent=2)
    
    # Resumen
    won = [t for t in trades if t["result"] == "WON"]
    lost = [t for t in trades if t["result"] == "LOST"]
    pending = [t for t in trades if t["result"] == "PENDING"]
    
    total_r = len(won) + len(lost)
    wr = len(won) / total_r * 100 if total_r > 0 else 0
    total_profit = sum(t.get("profit", 0) for t in won)
    total_loss = sum(t.get("profit", 0) for t in lost)
    
    print(f"\n{'='*60}")
    print(f"  RESULTADOS POR ESTRATEGIA")
    print(f"{'='*60}")
    
    strategies = {}
    for t in trades:
        s = t.get("strategy", "IA")
        if s not in strategies:
            strategies[s] = {"won": 0, "lost": 0, "pending": 0, "profit": 0, "loss": 0}
        if t["result"] == "WON":
            strategies[s]["won"] += 1
            strategies[s]["profit"] += t.get("profit", 0)
        elif t["result"] == "LOST":
            strategies[s]["lost"] += 1
            strategies[s]["loss"] += t.get("profit", 0)
        else:
            strategies[s]["pending"] += 1
    
    for s, d in strategies.items():
        total = d["won"] + d["lost"]
        wr_s = d["won"] / total * 100 if total > 0 else 0
        net = d["profit"] + d["loss"]
        print(f"\n  {s}:")
        print(f"    Ganadas: {d['won']} | Perdidas: {d['lost']} | Pendientes: {d['pending']}")
        print(f"    Win Rate: {wr_s:.0f}%")
        print(f"    Profit: ${d['profit']:+.2f} | Loss: ${d['loss']:.2f} | Neto: ${net:+.2f}")
    
    print(f"\n{'='*60}")
    print(f"  RESUMEN TOTAL")
    print(f"{'='*60}")
    print(f"  Ganadas: {len(won)} | Perdidas: {len(lost)} | Pendientes: {len(pending)}")
    print(f"  Win Rate: {wr:.1f}%")
    print(f"  Profit: ${total_profit:+.2f}")
    print(f"  Perdidas: ${total_loss:.2f}")
    print(f"  Neto: ${total_profit + total_loss:+.2f}")
    print(f"  Trades guardados: {len(trades)}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
