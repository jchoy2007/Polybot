"""
PolyBot - Fix Trade Results
============================
Revisa cada trade PENDING en trade_results.json
y lo marca como WON o LOST consultando Polymarket.
"""

import sys, json, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from urllib.request import urlopen, Request
from dotenv import load_dotenv
load_dotenv()

RESULTS_FILE = "data/trade_results.json"
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

def main():
    # Cargar trades
    try:
        with open(RESULTS_FILE) as f:
            trades = json.load(f)
    except:
        print("No se encontro trade_results.json")
        return

    # Obtener posiciones actuales
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")

    positions = {}
    if funder or pk:
        from web3 import Web3
        if pk:
            addr = Web3().eth.account.from_key(pk).address
        else:
            addr = funder

        for a in [funder, addr]:
            if not a:
                continue
            try:
                req = Request(f"{DATA_API}/positions?user={a.lower()}", headers={"User-Agent": "PolyBot"})
                with urlopen(req, timeout=15) as r:
                    data = json.loads(r.read())
                    if data:
                        for p in data:
                            title = (p.get("title") or p.get("question") or "").lower()
                            if title:
                                positions[title] = p
                        if positions:
                            break
            except:
                continue

    print(f"\n{'='*60}")
    print(f"  Fix Trade Results")
    print(f"  {len(trades)} trades totales")
    print(f"  {len(positions)} posiciones activas en Polymarket")
    print(f"{'='*60}\n")

    pending = [t for t in trades if t.get("result") == "PENDING"]
    print(f"  {len(pending)} trades PENDING para revisar\n")

    updated = 0
    for trade in trades:
        if trade.get("result") != "PENDING":
            continue

        q = trade.get("question", "")
        q_lower = q[:30].lower()

        # 1. Buscar en posiciones actuales
        found_pos = None
        for title, pos in positions.items():
            if q_lower in title:
                found_pos = pos
                break

        if found_pos:
            cur_value = float(found_pos.get("currentValue", 0) or 0)
            cur_price = float(found_pos.get("curPrice", 0) or 0)
            size = float(found_pos.get("size", 0) or 0)

            if cur_value <= 0.01 and size > 0:
                # Perdio
                trade["result"] = "LOST"
                trade["payout"] = 0
                trade["profit"] = -trade["amount"]
                updated += 1
                print(f"  LOST (valor=0): {q[:50]} | -${trade['amount']:.2f}")
            elif cur_price >= 0.95:
                # Gano (precio > 95c = casi seguro ganado)
                tokens = trade["amount"] / trade["price"]
                trade["result"] = "WON"
                trade["payout"] = round(tokens, 2)
                trade["profit"] = round(tokens - trade["amount"], 2)
                updated += 1
                print(f"  WON (precio>=95c): {q[:50]} | +${trade['profit']:.2f}")
            else:
                print(f"  PENDIENTE (precio={cur_price:.0%}): {q[:50]} | ${cur_value:.2f}")
        else:
            # No esta en posiciones = ya fue cobrada
            # Buscar en Gamma API
            mid = trade.get("market_id", "")
            if mid:
                try:
                    req = Request(f"{GAMMA_API}/markets/{mid}", headers={"User-Agent": "PolyBot"})
                    with urlopen(req, timeout=10) as r:
                        market = json.loads(r.read())

                    closed = market.get("closed", False)
                    resolved = market.get("resolved", False)

                    if closed or resolved:
                        # Mercado resuelto - determinar resultado
                        # Buscar el outcome ganador
                        outcome_prices = market.get("outcomePrices", "")
                        outcomes = market.get("outcomes", "")

                        try:
                            if isinstance(outcome_prices, str):
                                prices = json.loads(outcome_prices)
                            else:
                                prices = outcome_prices or []

                            if isinstance(outcomes, str):
                                outcome_names = json.loads(outcomes)
                            else:
                                outcome_names = outcomes or []
                        except:
                            prices = []
                            outcome_names = []

                        # Determinar ganador por precio (1.0 = gano, 0.0 = perdio)
                        trade_side = trade.get("side", "").upper()
                        won = False

                        if prices and outcome_names:
                            for i, price in enumerate(prices):
                                p = float(price)
                                name = outcome_names[i] if i < len(outcome_names) else ""
                                if p >= 0.95 and trade_side.upper() in name.upper():
                                    won = True
                                    break
                                elif p >= 0.95 and name.upper() in trade_side.upper():
                                    won = True
                                    break

                        # Tambien checar YES/NO
                        if not won and prices:
                            yes_price = float(prices[0]) if len(prices) > 0 else 0
                            no_price = float(prices[1]) if len(prices) > 1 else 0

                            if trade_side in ["YES", "UP"] and yes_price >= 0.95:
                                won = True
                            elif trade_side in ["NO", "DOWN"] and no_price >= 0.95:
                                won = True
                            elif trade_side in ["YES", "UP"] and yes_price <= 0.05:
                                won = False
                            elif trade_side in ["NO", "DOWN"] and no_price <= 0.05:
                                won = False

                        if won:
                            tokens = trade["amount"] / trade["price"]
                            trade["result"] = "WON"
                            trade["payout"] = round(tokens, 2)
                            trade["profit"] = round(tokens - trade["amount"], 2)
                            updated += 1
                            print(f"  WON (cobrada): {q[:50]} | +${trade['profit']:.2f}")
                        else:
                            trade["result"] = "LOST"
                            trade["payout"] = 0
                            trade["profit"] = -trade["amount"]
                            updated += 1
                            print(f"  LOST (cobrada): {q[:50]} | -${trade['amount']:.2f}")
                    else:
                        print(f"  PENDIENTE (mercado abierto): {q[:50]}")
                except Exception as e:
                    print(f"  ERROR revisando: {q[:50]} | {e}")
            else:
                print(f"  SIN MARKET_ID: {q[:50]}")

    # Guardar
    if updated > 0:
        with open(RESULTS_FILE, "w") as f:
            json.dump(trades, f, indent=2)
        print(f"\n  Actualizados: {updated} trades")

    # Resumen final
    won = [t for t in trades if t["result"] == "WON"]
    lost = [t for t in trades if t["result"] == "LOST"]
    pending = [t for t in trades if t["result"] == "PENDING"]

    total_r = len(won) + len(lost)
    wr = len(won) / total_r * 100 if total_r > 0 else 0
    profit = sum(t.get("profit", 0) for t in won)
    losses = sum(t.get("profit", 0) for t in lost)

    print(f"\n{'='*60}")
    print(f"  RESUMEN REAL")
    print(f"{'='*60}")
    print(f"  Ganadas: {len(won)}")
    print(f"  Perdidas: {len(lost)}")
    print(f"  Pendientes: {len(pending)}")
    print(f"  Win Rate: {wr:.1f}%")
    print(f"  Profit: ${profit:+.2f}")
    print(f"  Perdidas: ${losses:.2f}")
    print(f"  Neto: ${profit + losses:+.2f}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
