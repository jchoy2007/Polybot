#!/usr/bin/env python3
"""
PolyBot — ONBOARDING / Situational Report
==========================================
Corre esto PRIMERO al abrir una sesión nueva (tú o Claude) para entender
en 1 pantalla TODO lo que está pasando: estrategias activas, balance real,
win-rate por estrategia, últimos trades y cambios recientes.

El contexto ESTÁTICO (qué es el bot, decisiones, learnings) vive en CLAUDE.md
y Claude Code lo lee solo al abrir el repo. Este script añade el estado VIVO
que CLAUDE.md no puede tener (números que cambian cada día).

USO:
    ./venv/bin/python scripts/onboard.py
    ./venv/bin/python scripts/onboard.py --no-balance   # más rápido, sin RPC
"""

import os, sys, json, subprocess, argparse, re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

B = "\033[1m"; G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"; X = "\033[0m"


def hr(t=""):
    print(f"\n{B}{'═'*64}{X}")
    if t:
        print(f"{B}  {t}{X}")
        print(f"{B}{'═'*64}{X}")


def header_for_claude():
    print(f"{B}{C}")
    print("  ┌────────────────────────────────────────────────────────────┐")
    print("  │  POLYBOT — REPORTE DE SITUACIÓN (léeme primero)             │")
    print("  └────────────────────────────────────────────────────────────┘")
    print(X)
    print("  Si eres Claude en una sesión nueva:")
    print("   1. Ya leíste CLAUDE.md (contexto, decisiones, learnings caros).")
    print("   2. Este reporte = estado VIVO de hoy. Úsalo antes de proponer cambios.")
    print("   3. Regla de oro: maneja dinero real. Cambios solo con data o bug claro.")
    print(f"  Fecha del reporte: {datetime.now().strftime('%Y-%m-%d %H:%M')} (hora del VPS)")


def show_strategy_flags():
    hr("ESTRATEGIAS ACTIVAS (flags en config/settings.py)")
    try:
        from config.settings import SAFETY
        flags = [
            ("📈 STOCKS",   getattr(SAFETY, "enable_stock_trader", True)),
            ("⚽ FOOTBALL",  getattr(SAFETY, "enable_football_trader", True)),
            ("🏛️ POLITICS",  getattr(SAFETY, "enable_politics_trader", True)),
        ]
        for name, on in flags:
            print(f"   {G}● ON {X}{name}" if on else f"   {R}○ OFF{X}{name}")
        intraday = getattr(SAFETY, "enable_intraday_updown", False)
        print(f"\n   Sub-filtros stocks:")
        print(f"     'Up or Down' intradía: {(G+'ON') if intraday else (R+'OFF (coin-flip, -$57)')}{X}")
        print(f"   Sizing: Kelly {SAFETY.kelly_fraction} | min_edge {SAFETY.min_edge_required:.0%} | "
              f"max apuesta ${SAFETY.max_bet_absolute:.0f} ({SAFETY.max_bet_pct:.0%} bankroll)")
    except Exception as e:
        print(f"   {R}Error leyendo settings: {e}{X}")


def show_balance(skip=False):
    hr("CAPITAL REAL (pUSD en funder Polymarket v2)")
    if skip:
        print(f"   {Y}(omitido con --no-balance){X}")
        return
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
        bal = pusd.functions.balanceOf(w3.to_checksum_address(funder)).call() / 1e6
        col = G if bal >= 60 else (Y if bal >= 45 else R)
        print(f"   💰 Balance libre: {col}${bal:.2f}{X}")
        print(f"   Kill-switch dinámico: piso ≈ 70% del valor total desde el último arranque.")
    except Exception as e:
        print(f"   {Y}No se pudo leer balance ({str(e)[:50]}). Usa --no-balance o revisa .env/RPC.{X}")


def show_winrate():
    hr("WIN-RATE POR ESTRATEGIA (data/trade_results.json)")
    p = Path("data/trade_results.json")
    if not p.exists():
        print(f"   {R}sin trade_results.json{X}"); return
    trades = json.load(open(p))
    agg = defaultdict(lambda: [0, 0, 0, 0.0])  # resueltos, won, pend, pnl
    for t in trades:
        s = t.get("strategy", "?")
        r = t.get("result", "")
        if r in ("WON", "LOST"):
            agg[s][0] += 1
            if r == "WON":
                agg[s][1] += 1
            agg[s][3] += float(t.get("profit", 0) or 0)
        else:
            agg[s][2] += 1
    print(f"   {'ESTRATEGIA':<12}{'RES':>5}{'WON':>5}{'WR':>6}{'PEND':>6}{'P&L':>10}")
    for s, (res, won, pend, pnl) in sorted(agg.items()):
        wr = f"{won/res*100:.0f}%" if res else "—"
        col = G if pnl >= 0 else R
        print(f"   {s:<12}{res:>5}{won:>5}{wr:>6}{pend:>6}{col}{f'${pnl:+.2f}':>10}{X}")


def show_recent_trades(n=8):
    hr(f"ÚLTIMOS {n} TRADES")
    p = Path("data/trade_results.json")
    if not p.exists():
        return
    trades = json.load(open(p))
    for t in trades[-n:]:
        r = t.get("result", "PEND")
        col = G if r == "WON" else (R if r == "LOST" else Y)
        print(f"   {t.get('timestamp','?')[:16]}  {t.get('strategy','?'):<9} "
              f"{col}{r:<5}{X} ${t.get('amount','?')} "
              f"edge={round(float(t.get('edge') or 0),3)}  {t.get('question','')[:42]}")


def show_service_and_git():
    hr("SERVICIO + CAMBIOS RECIENTES")
    try:
        st = subprocess.run(["systemctl", "is-active", "polybot"],
                            capture_output=True, text=True).stdout.strip()
        col = G if st == "active" else R
        print(f"   Servicio polybot: {col}{st}{X}")
    except Exception:
        print(f"   {Y}systemctl no disponible (¿no es el VPS?){X}")
    try:
        log = subprocess.run(["git", "log", "--oneline", "-8"],
                            capture_output=True, text=True).stdout.strip()
        print(f"\n   Últimos commits:")
        for line in log.split("\n"):
            print(f"     {line}")
    except Exception as e:
        print(f"   {Y}git log no disponible: {e}{X}")


def show_next_steps():
    hr("EN QUÉ ESTAMOS (al 2-Jun-2026)")
    print("""   • Modo recuperación: SOLO stocks (tech, target-based) + fútbol.
   • Politics OFF (ganaba 95% pero P&L casi nulo, drenaba a edge bajo).
   • Stocks: 'Up or Down' intradía OFF (30% WR, -$57, edge invertido).
   • Fútbol: nuevo para Mundial 2026 (11-Jun), Elo de ClubElo, apuesta
     SOLO entre 24h y 1h antes del kickoff.
   • A VIGILAR: las próximas ~10-15 apuestas tech (confirmar 68% WR en vivo)
     y los primeros mercados del Mundial.
   • NO correr recovery.py (incompatible, crashea — ver CLAUDE.md).""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-balance", action="store_true", help="No consultar el RPC (más rápido)")
    args = ap.parse_args()

    header_for_claude()
    show_strategy_flags()
    show_balance(skip=args.no_balance)
    show_winrate()
    show_recent_trades()
    show_service_and_git()
    show_next_steps()
    print(f"\n{B}{C}  → Detalle completo y el 'por qué' de cada decisión: lee CLAUDE.md{X}\n")


if __name__ == "__main__":
    main()
