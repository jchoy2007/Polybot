#!/usr/bin/env python3
"""
Backtest simple: aplicar filtros actuales retroactivamente a
data/trade_results.json y comparar vs resultados reales.

Limitación conocida: trades anteriores al 15-Abr no tienen campo
`edge` ni `prob` registrado, así que el filtro por edge sólo se aplica
donde esos campos existen. El backtest es conservador — marca como
"bloqueado" solo lo que sí se puede verificar con la data disponible.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

# Filtros actuales (según CLAUDE.md sección "Filtros activos 22 abril 2026")
SPORTS_MIN_PRICE = 0.50
SPORTS_MAX_PRICE = 0.80
SPORTS_MIN_EDGE = 0.06
SPORTS_MIN_PROB = 0.60
STOCKS_MIN_EDGE = 0.08
CRYPTO_MIN_EDGE = 0.05
CRYPTO_MIN_PROB = 0.55
EXTREME_MIN = 0.03   # rechazar precios de cola larga
EXTREME_MAX = 0.97

# Derivados de esports (bloqueados universal desde ba6162c)
DERIVATIVE_KEYWORDS = [
    "games total", "map handicap", "game handicap",
    "game 1 winner", "game 2 winner", "game 3 winner",
]


def is_derivative(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in DERIVATIVE_KEYWORDS)


def would_pass_filters(t: dict) -> tuple[bool, str]:
    """Devuelve (pasa, razón_de_bloqueo_si_aplica)."""
    q = t.get("question") or ""
    strategy = t.get("strategy") or ""
    price = float(t.get("price") or 0)
    edge = t.get("edge")  # puede ser None en trades viejos
    prob = t.get("prob")

    if is_derivative(q):
        return False, "derivado esports (ba6162c)"
    if price < EXTREME_MIN or price > EXTREME_MAX:
        return False, f"precio extremo {price:.2f}"

    if strategy == "SPORTS":
        if price < SPORTS_MIN_PRICE or price > SPORTS_MAX_PRICE:
            return False, f"SPORTS market_price {price:.2f} fuera [0.50, 0.80]"
        if edge is not None and edge < SPORTS_MIN_EDGE:
            return False, f"SPORTS edge {edge*100:.1f}% < 6%"
        if prob is not None and prob < SPORTS_MIN_PROB:
            return False, f"SPORTS prob {prob*100:.0f}% < 60%"
    elif strategy == "STOCKS":
        if edge is not None and edge < STOCKS_MIN_EDGE:
            return False, f"STOCKS edge {edge*100:.1f}% < 8%"
    elif strategy == "CRYPTO":
        if edge is not None and edge < CRYPTO_MIN_EDGE:
            return False, f"CRYPTO edge {edge*100:.1f}% < 5%"
        if prob is not None and prob < CRYPTO_MIN_PROB:
            return False, f"CRYPTO prob {prob*100:.0f}% < 55%"

    return True, ""


def main():
    path = Path("data/trade_results.json")
    trades = json.loads(path.read_text())

    # Sólo trades resueltos (WON/LOST), ignorar PENDING
    resolved = [t for t in trades if t.get("result") in ("WON", "LOST")]

    cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
    recent = [t for t in resolved
              if datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
                 .replace(tzinfo=timezone.utc).timestamp() >= cutoff]

    print("=" * 60)
    print(f"  BACKTEST — últimos 30 días ({len(recent)} trades resueltos)")
    print("=" * 60)

    real_wins = sum(1 for t in recent if t["result"] == "WON")
    real_pnl = sum(float(t.get("profit") or 0) for t in recent)
    real_wr = real_wins / len(recent) * 100 if recent else 0

    kept = []
    blocked = []
    reasons = {}
    for t in recent:
        ok, reason = would_pass_filters(t)
        (kept if ok else blocked).append(t)
        if not ok:
            reasons[reason] = reasons.get(reason, 0) + 1

    sim_wins = sum(1 for t in kept if t["result"] == "WON")
    sim_pnl = sum(float(t.get("profit") or 0) for t in kept)
    sim_wr = sim_wins / len(kept) * 100 if kept else 0

    blocked_pnl = sum(float(t.get("profit") or 0) for t in blocked)
    blocked_wins = sum(1 for t in blocked if t["result"] == "WON")

    print(f"\nREAL:")
    print(f"  Trades:    {len(recent)}")
    print(f"  Win rate:  {real_wr:.1f}% ({real_wins}/{len(recent)})")
    print(f"  P&L neto:  ${real_pnl:+.2f}")

    print(f"\nCON FILTROS ACTUALES retroactivos:")
    print(f"  Trades:    {len(kept)} ({len(blocked)} bloqueados)")
    print(f"  Win rate:  {sim_wr:.1f}% ({sim_wins}/{len(kept)})")
    print(f"  P&L neto:  ${sim_pnl:+.2f}")

    delta = sim_pnl - real_pnl
    arrow = "🟢" if delta > 0 else "🔴" if delta < 0 else "⚪"
    print(f"\n{arrow} Diferencia P&L: ${delta:+.2f}")
    print(f"   Win rate: {sim_wr - real_wr:+.1f}pp")

    if blocked:
        print(f"\n📉 Bloqueados ({len(blocked)}): "
              f"{blocked_wins} habrían ganado, "
              f"{len(blocked) - blocked_wins} habrían perdido, "
              f"P&L evitado ${-blocked_pnl:+.2f}")
        print(f"\n   Razones de bloqueo:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"      {count:>3}× {reason}")

    # Por estrategia
    print(f"\n📊 Por estrategia (real vs simulado):")
    strategies = set(t.get("strategy", "?") for t in recent)
    for s in sorted(strategies):
        s_recent = [t for t in recent if t.get("strategy") == s]
        s_kept = [t for t in kept if t.get("strategy") == s]
        s_rw = sum(1 for t in s_recent if t["result"] == "WON")
        s_kw = sum(1 for t in s_kept if t["result"] == "WON")
        s_rp = sum(float(t.get("profit") or 0) for t in s_recent)
        s_kp = sum(float(t.get("profit") or 0) for t in s_kept)
        s_rwr = s_rw / len(s_recent) * 100 if s_recent else 0
        s_kwr = s_kw / len(s_kept) * 100 if s_kept else 0
        print(f"  {s:<8} real {s_rw}/{len(s_recent)} ({s_rwr:.0f}%) ${s_rp:+.2f}  "
              f"→ sim {s_kw}/{len(s_kept)} ({s_kwr:.0f}%) ${s_kp:+.2f}")

    print("=" * 60)


if __name__ == "__main__":
    main()
