#!/usr/bin/env python3
"""
Dashboard web para PolyBot.
Corre en puerto 5000, accesible desde navegador.

Uso: ./venv/bin/python dashboard/server.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
TRADES_FILE = DATA_DIR / "trade_results.json"
LOGS_DIR = ROOT / "logs"

app = Flask(__name__, static_folder=str(Path(__file__).parent))


def _load_trades():
    if not TRADES_FILE.exists():
        return []
    with open(TRADES_FILE) as f:
        return json.load(f)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def status():
    try:
        trades = _load_trades()
        won = sum(1 for t in trades if t.get("result") == "WON")
        lost = sum(1 for t in trades if t.get("result") == "LOST")
        pending = sum(1 for t in trades if t.get("result") == "PENDING")
        resolved = won + lost
        wr = (won / resolved * 100) if resolved else 0.0
        net = sum(t.get("profit", 0) or 0 for t in trades if t.get("result") in ("WON", "LOST"))
        invested_open = sum(t.get("amount", 0) or 0 for t in trades if t.get("result") == "PENDING")

        # Bankroll desde el log más reciente (línea "Bankroll: $XX")
        bankroll = None
        try:
            log_files = sorted(LOGS_DIR.glob("polybot_*.log"))
            if log_files:
                with open(log_files[-1], errors="ignore") as f:
                    for line in reversed(f.readlines()[-1000:]):
                        if "Bankroll:" in line and "$" in line:
                            seg = line.split("Bankroll:")[1].split("|")[0]
                            bankroll = float(seg.strip().lstrip("$"))
                            break
        except Exception:
            pass

        return jsonify({
            "wins": won,
            "losses": lost,
            "pending": pending,
            "resolved": resolved,
            "win_rate": round(wr, 1),
            "net_pnl": round(net, 2),
            "invested_open": round(invested_open, 2),
            "bankroll": bankroll,
            "total_trades": len(trades),
            "last_update": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/positions")
def positions():
    try:
        trades = _load_trades()
        pending = [t for t in trades if t.get("result") == "PENDING"]
        pending.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
        return jsonify(pending)
    except Exception:
        return jsonify([])


@app.route("/api/recent")
def recent():
    try:
        trades = _load_trades()
        trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
        return jsonify(trades[:20])
    except Exception:
        return jsonify([])


@app.route("/api/filters")
def filters():
    """Cuenta cuántas veces saltó cada filtro hoy (parseando logs)."""
    today = datetime.now().strftime("%Y%m%d")
    log_path = LOGS_DIR / f"polybot_{today}.log"
    if not log_path.exists():
        log_files = sorted(LOGS_DIR.glob("polybot_*.log"))
        if not log_files:
            return jsonify({})
        log_path = log_files[-1]

    triggers = {
        "Fuera de horario US": "Fuera de horario US",
        "Weekend (skip stocks)": "weekend",
        "VIX alto": "VIX",
        "Tendencia S&P": "S&P tendencia",
        "News bearish": "News:.*BEARISH",
        "Daily loss limit": "Daily loss limit",
        "Direcciones opuestas": "direcciones opuestas",
        "Solo Up/Down": "Solo Up/Down",
        "Edge insuficiente": "Edge insuficiente",
        "Ya operado hoy": "ya operado",
        "Liquidez baja": "liquidez baja",
    }
    counts = {k: 0 for k in triggers}
    try:
        with open(log_path, errors="ignore") as f:
            content = f.read()
        for label, pat in triggers.items():
            counts[label] = content.count(pat) if pat == pat else 0
            counts[label] = sum(1 for line in content.splitlines() if pat.lower() in line.lower())
    except Exception:
        pass
    return jsonify({k: v for k, v in counts.items() if v > 0})


if __name__ == "__main__":
    print(f"📊 PolyBot Dashboard — sirviendo desde {ROOT}")
    print("   Abrir http://localhost:5000  (o http://TU_IP:5000 si VPS)")
    app.run(host="0.0.0.0", port=5000, debug=False)
