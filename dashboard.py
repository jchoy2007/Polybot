"""
PolyBot - Dashboard v5 ✦ Neon Edition
=======================================
"Mi magia es no rendirme nunca"

USO: python dashboard.py
"""

import os, sys, json, subprocess, socket, signal, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 8080
DATA_DIR = "data"
LOGS_DIR = "logs"
PID_FILE = "data/bot.pid"
BOT_DIR = os.path.dirname(os.path.abspath(__file__))

_pos_cache = {"data": [], "ts": 0}


def detect_bot_process():
    """Detecta si main.py está corriendo en CUALQUIER terminal."""
    # 1. Primero: PID file (más confiable)
    try:
        with open(PID_FILE) as f:
            info = json.load(f)
            pid = info.get("pid")
            if pid and pid_alive(pid):
                return pid
            else:
                # PID file existe pero proceso muerto → limpiar
                try: os.remove(PID_FILE)
                except: pass
    except:
        pass

    # 2. Buscar proceso python con main.py en Windows
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                'tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH',
                capture_output=True, text=True, shell=True, timeout=5
            )
            # Si no hay ningun python.exe corriendo, no hay bot
            if "python.exe" not in r.stdout.lower():
                return None

            # Hay python corriendo, verificar si es main.py --live
            r2 = subprocess.run(
                'wmic process where "name=\'python.exe\'" get commandline,processid /format:csv',
                capture_output=True, text=True, shell=True, timeout=5
            )
            for line in r2.stdout.strip().split("\n"):
                if "main.py" in line and "--live" in line:
                    parts = line.strip().split(",")
                    for part in reversed(parts):
                        part = part.strip()
                        if part.isdigit():
                            return int(part)
        else:
            r = subprocess.run(["pgrep", "-f", "main.py.*--live"],
                             capture_output=True, text=True, timeout=5)
            if r.stdout.strip():
                return int(r.stdout.strip().split("\n")[0])
    except:
        pass

    return None


def pid_alive(pid):
    try:
        if sys.platform == "win32":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        else:
            os.kill(pid, 0); return True
    except:
        return False


def write_pid(pid, mode):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PID_FILE, "w") as f:
        json.dump({"pid": pid, "mode": mode, "since": datetime.now().strftime("%H:%M")}, f)


def clear_pid():
    try: os.remove(PID_FILE)
    except: pass


def is_bot_running():
    pid = detect_bot_process()
    return pid is not None


def get_bot_info():
    pid = detect_bot_process()
    if pid:
        try:
            with open(PID_FILE) as f:
                info = json.load(f)
                return {"running": True, "mode": info.get("mode", "?"), "since": info.get("since", "?"), "pid": pid}
        except:
            return {"running": True, "mode": "externo", "since": "?", "pid": pid}
    return {"running": False, "mode": None, "since": None, "pid": None}


def start_bot(mode="24/7"):
    if is_bot_running():
        return {"success": False, "output": "⚠️ Bot ya está corriendo"}
    cmd = [sys.executable, "main.py", "--live"]
    if mode == "stop-at-18":
        cmd.extend(["--stop-at", "18"])
    try:
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kw["start_new_session"] = True
        proc = subprocess.Popen(cmd, cwd=BOT_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        write_pid(proc.pid, mode)
        lab = "24/7" if mode == "24/7" else "→18:00"
        return {"success": True, "output": f"🚀 Bot iniciado ({lab})\nPID: {proc.pid}"}
    except Exception as e:
        return {"success": False, "output": f"❌ {str(e)}"}


def stop_bot():
    pid = detect_bot_process()
    if not pid:
        return {"success": False, "output": "Bot no está corriendo"}
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGTERM)
        time.sleep(1); clear_pid()
        return {"success": True, "output": f"⏹ Bot detenido (PID {pid})"}
    except Exception as e:
        clear_pid()
        return {"success": True, "output": f"Bot parado: {str(e)[:40]}"}


def fetch_positions():
    global _pos_cache
    if time.time() - _pos_cache["ts"] < 300:
        return _pos_cache["data"]
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not pk: return []
    try:
        from web3 import Web3
        import re
        addr = Web3().eth.account.from_key(pk).address
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        for a in [funder, addr]:
            if not a: continue
            try:
                req = Request(f"https://data-api.polymarket.com/positions?user={a.lower()}", headers={"User-Agent": "PolyBot"})
                with urlopen(req, timeout=15) as r:
                    data = json.loads(r.read())
                    if data and isinstance(data, list):
                        pos = []
                        now = datetime.now()
                        for p in data:
                            v = float(p.get("currentValue", 0) or 0)
                            if v > 0.01:
                                title = p.get("title") or p.get("question") or "?"
                                resolve_txt = "?"
                                months = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
                                          "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
                                tl = title.lower()
                                target_date = None
                                m = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})', tl)
                                if m:
                                    mon = months.get(m.group(1), 0)
                                    day = int(m.group(2))
                                    if mon and 1 <= day <= 31:
                                        target_date = datetime(now.year, mon, day, 23, 59)
                                if not target_date:
                                    m2 = re.search(r'(\d{4})-(\d{2})-(\d{2})', tl)
                                    if m2:
                                        target_date = datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)), 23, 59)
                                if target_date:
                                    diff_h = (target_date - now).total_seconds() / 3600
                                    if diff_h <= 0:
                                        resolve_txt = "Ahora"
                                    elif diff_h < 1:
                                        resolve_txt = f"{int(diff_h*60)}m"
                                    elif diff_h < 48:
                                        resolve_txt = f"{int(diff_h)}h"
                                    else:
                                        resolve_txt = f"{int(diff_h)}h"
                                else:
                                    if "billboard" in tl or "week of" in tl:
                                        resolve_txt = "168h+"
                                    elif "opening weekend" in tl:
                                        resolve_txt = "48-72h"
                                    elif "netflix" in tl or "season" in tl:
                                        resolve_txt = "48-72h"
                                pos.append({"title": title[:40],
                                            "side": p.get("outcome") or "?", "value": v,
                                            "pnl": float(p.get("cashPnl", 0) or 0),
                                            "price": float(p.get("curPrice", 0) or 0),
                                            "resolve": resolve_txt})
                        if pos:
                            pos.sort(key=lambda x: x["value"], reverse=True)
                            _pos_cache = {"data": pos, "ts": time.time()}
                            return pos
            except: continue
    except: pass
    return _pos_cache["data"]


def build_data():
    trades = []
    try:
        with open(f"{DATA_DIR}/trade_results.json") as f: trades = json.load(f)
    except: pass
    summary = {}
    try:
        sums = sorted(Path(DATA_DIR).glob("summary_*.json"))
        if sums:
            with open(sums[-1]) as f: summary = json.load(f)
    except: pass

    # Leer balance REAL del log del bot (más preciso que summary)
    bal = 0
    try:
        lf = Path(LOGS_DIR) / f"polybot_{datetime.now().strftime('%Y%m%d')}.log"
        if lf.exists():
            with open(lf, "r", encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    # Buscar "Bankroll: $XXX" o "Balance real USDC.e: $XXX"
                    if "Bankroll: $" in line:
                        import re
                        m = re.search(r'Bankroll: \$([0-9,.]+)', line)
                        if m:
                            bal = float(m.group(1).replace(",", ""))
                            break
                    elif "Balance real USDC.e: $" in line:
                        import re
                        m = re.search(r'Balance real USDC\.e: \$([0-9,.]+)', line)
                        if m:
                            bal = float(m.group(1).replace(",", ""))
                            break
    except: pass

    # Fallback: summary, bot_history, last trade
    if bal == 0:
        bal = summary.get("bankroll_actual", 0)
    if bal == 0:
        try:
            with open(f"{DATA_DIR}/bot_history.json") as f:
                h = json.load(f)
                bal = h.get("stats", {}).get("balance", 0)
        except: pass
    if bal == 0 and trades:
        last = sorted(trades, key=lambda x: x.get("timestamp",""))
        if last:
            bal = last[-1].get("bankroll_after", 0)
    summary["bankroll_actual"] = bal
    logs = ""
    try:
        lf = Path(LOGS_DIR) / f"polybot_{datetime.now().strftime('%Y%m%d')}.log"
        if lf.exists():
            with open(lf, "r", encoding="utf-8") as f: logs = "".join(f.readlines()[-35:])
    except: pass
    # Stats: Leer del log del bot (resultados confirmados por el bot)
    import re as _re
    bot_won = 0; bot_lost = 0; bot_pending = 0; bot_profit = 0; bot_loss = 0
    try:
        lf2 = Path(LOGS_DIR) / f"polybot_{datetime.now().strftime('%Y%m%d')}.log"
        if lf2.exists():
            with open(lf2, "r", encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    if "WIN RATE:" in line:
                        wr_m = _re.search(r'WIN RATE: (\d+)/(\d+)', line)
                        if wr_m:
                            bot_won = int(wr_m.group(1))
                            total_resolved = int(wr_m.group(2))
                            bot_lost = total_resolved - bot_won
                        pr_m = _re.search(r'Profit: \$\+?([0-9.]+)', line)
                        if pr_m: bot_profit = float(pr_m.group(1))
                        ls_m = _re.search(r'rdidas: \$-?([0-9.]+)', line)
                        if ls_m: bot_loss = float(ls_m.group(1))
                        pn_m = _re.search(r'Pendientes: (\d+)', line)
                        if pn_m: bot_pending = int(pn_m.group(1))
                        break
    except: pass
    total_resolved = bot_won + bot_lost
    wr = round(bot_won / total_resolved * 100, 1) if total_resolved else 0
    net = round(bot_profit - bot_loss, 2)

    # Estrategias del log del bot (más preciso)
    strategies = {}
    try:
        lf3 = Path(LOGS_DIR) / f"polybot_{datetime.now().strftime('%Y%m%d')}.log"
        if lf3.exists():
            with open(lf3, "r", encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    if "WIN RATE:" in line:
                        # Parse strategy lines that follow
                        break
                    strat_m = _re.match(r'\s+(IA|HARVEST|WEATHER|STOCKS|CRYPTO|FLASH_CRASH): (\d+)/(\d+).*P&L: \$([+-]?[0-9.]+).*Pendientes: (\d+)', line.strip())
                    if strat_m:
                        sn = strat_m.group(1)
                        sw = int(strat_m.group(2))
                        st = int(strat_m.group(3))
                        sp = float(strat_m.group(4))
                        spe = int(strat_m.group(5))
                        strategies[sn] = {"won": sw, "lost": st - sw, "pending": spe, "profit": sp}
    except: pass
    for s in ["IA", "CRYPTO", "HARVEST", "WEATHER", "STOCKS", "FLASH_CRASH"]:
        if s not in strategies: strategies[s] = {"won": 0, "lost": 0, "pending": 0, "profit": 0}

    positions = fetch_positions()
    history = {}
    try:
        with open(f"{DATA_DIR}/bot_history.json") as f: history = json.load(f)
    except: pass
    since = history.get("first_start", "2026-04-02")[:10]
    days_running = (datetime.now() - datetime.fromisoformat(since)).days + 1
    return {
        "ts": datetime.now().strftime("%H:%M:%S"), "summary": summary, "bot": get_bot_info(),
        "stats": {"total": total_resolved + bot_pending, "won": bot_won, "lost": bot_lost,
                  "pending": bot_pending, "wr": wr, "net": net},
        "strategies": strategies, "positions": positions,
        "pos_total": round(sum(p["value"] for p in positions),2), "logs": logs,
        "since": since, "days": days_running,
    }


HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>PolyBot</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#06090f;color:#e6edf3;padding:0;margin:0}
.wrap{max-width:600px;margin:0 auto;padding:12px}
.banner{background:linear-gradient(135deg,#0a0e1a 0%,#1a0a2e 50%,#0a1a2e 100%);padding:16px;text-align:center;border-bottom:1px solid rgba(163,113,247,.2);position:relative;overflow:hidden}
.banner::after{content:'';position:absolute;top:0;left:-50%;width:200%;height:100%;background:linear-gradient(90deg,transparent,rgba(163,113,247,.05),transparent);animation:sh 4s infinite}
@keyframes sh{0%{transform:translateX(-50%)}100%{transform:translateX(50%)}}
.banner h1{font-size:22px;font-weight:800;background:linear-gradient(90deg,#a371f7,#58a6ff,#3fb950);-webkit-background-clip:text;-webkit-text-fill-color:transparent;letter-spacing:2px}
.quote{font-size:11px;color:rgba(163,113,247,.7);margin-top:6px;font-style:italic;letter-spacing:.5px}
.quote b{color:#a371f7;font-style:normal}
.hd{display:flex;justify-content:space-between;align-items:center;margin:10px 0}
.tm{font-size:11px;color:#484f58}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:4px}
.don{background:#3fb950;box-shadow:0 0 10px #3fb950,0 0 20px rgba(63,185,80,.3);animation:p 1.5s infinite}
.doff{background:#f85149;box-shadow:0 0 10px #f85149}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.sb{display:flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;margin-bottom:10px;font-size:12px;font-weight:600}
.sb-on{background:rgba(63,185,80,.06);border:1px solid rgba(63,185,80,.2);color:#3fb950}
.sb-off{background:rgba(248,81,73,.06);border:1px solid rgba(248,81,73,.2);color:#f85149}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:10px}
.c{background:rgba(13,17,23,.9);border:1px solid rgba(88,166,255,.08);border-radius:10px;padding:10px;position:relative;overflow:hidden}
.c::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(88,166,255,.3),transparent)}
.c .l{font-size:9px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
.c .v{font-size:20px;font-weight:700}
.gr{color:#3fb950;text-shadow:0 0 10px rgba(63,185,80,.3)}
.rd{color:#f85149;text-shadow:0 0 10px rgba(248,81,73,.3)}
.yl{color:#d29922;text-shadow:0 0 8px rgba(210,153,34,.2)}
.bl{color:#58a6ff;text-shadow:0 0 10px rgba(88,166,255,.3)}
.pp{color:#a371f7;text-shadow:0 0 10px rgba(163,113,247,.3)}
h2{font-size:10px;color:#a371f7;margin:14px 0 6px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px}
.cr{display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px;margin-bottom:5px}
.cr2{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:12px}
.bt{border:none;border-radius:8px;color:#fff;padding:12px 6px;font-size:11px;font-weight:700;cursor:pointer;text-align:center;-webkit-tap-highlight-color:transparent;transition:all .12s;letter-spacing:.3px;position:relative;overflow:hidden}
.bt::before{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(255,255,255,.1),transparent);pointer-events:none}
.bt:active{transform:scale(.94);filter:brightness(1.3)}
.bt:disabled{opacity:.2;pointer-events:none}
.b1{background:linear-gradient(135deg,#238636,#2ea043);box-shadow:0 0 20px rgba(46,160,67,.3),inset 0 1px rgba(255,255,255,.1)}
.b2{background:linear-gradient(135deg,#1f6feb,#388bfd);box-shadow:0 0 20px rgba(56,139,253,.3),inset 0 1px rgba(255,255,255,.1)}
.b3{background:linear-gradient(135deg,#b62324,#da3633);box-shadow:0 0 20px rgba(218,54,51,.3),inset 0 1px rgba(255,255,255,.1)}
.b4{background:linear-gradient(135deg,#9e6a03,#d29922);box-shadow:0 0 15px rgba(210,153,34,.3)}
.b5{background:rgba(13,17,23,.8);border:1px solid rgba(163,113,247,.2);box-shadow:0 0 10px rgba(163,113,247,.08)}
.sr{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-radius:8px;margin-bottom:4px;position:relative;overflow:hidden}
.sr::before{content:'';position:absolute;left:0;top:0;bottom:0;width:2px}
.sr::after{content:'';position:absolute;left:0;top:0;bottom:0;width:40px;opacity:.06}
.s0{background:rgba(88,166,255,.04);border:1px solid rgba(88,166,255,.1)}.s0::before{background:#58a6ff;box-shadow:0 0 8px #58a6ff}.s0::after{background:linear-gradient(90deg,#58a6ff,transparent)}
.s1{background:rgba(63,185,80,.04);border:1px solid rgba(63,185,80,.1)}.s1::before{background:#3fb950;box-shadow:0 0 8px #3fb950}.s1::after{background:linear-gradient(90deg,#3fb950,transparent)}
.s2{background:rgba(210,153,34,.04);border:1px solid rgba(210,153,34,.1)}.s2::before{background:#d29922;box-shadow:0 0 8px #d29922}.s2::after{background:linear-gradient(90deg,#d29922,transparent)}
.s3{background:rgba(163,113,247,.04);border:1px solid rgba(163,113,247,.1)}.s3::before{background:#a371f7;box-shadow:0 0 8px #a371f7}.s3::after{background:linear-gradient(90deg,#a371f7,transparent)}
.s4{background:rgba(240,136,62,.04);border:1px solid rgba(240,136,62,.1)}.s4::before{background:#f0883e;box-shadow:0 0 8px #f0883e}.s4::after{background:linear-gradient(90deg,#f0883e,transparent)}
.s5{background:rgba(248,81,73,.04);border:1px solid rgba(248,81,73,.1)}.s5::before{background:#f85149;box-shadow:0 0 8px #f85149}.s5::after{background:linear-gradient(90deg,#f85149,transparent)}
.sr .n{font-size:12px;font-weight:600;padding-left:6px}
.sr .d{text-align:right;font-size:11px;font-weight:500}
.pos{padding:6px 10px;border-radius:6px;margin-bottom:3px;display:flex;justify-content:space-between;align-items:center;font-size:11px;background:rgba(13,17,23,.5);border:1px solid rgba(255,255,255,.03)}
.pos .pt{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-right:6px;color:#8b949e}
.pos .pv{text-align:right;font-weight:600;white-space:nowrap;font-size:12px}
.lb{background:rgba(1,4,9,.5);border:1px solid rgba(163,113,247,.06);border-radius:8px;padding:8px;font-family:Consolas,'SF Mono',monospace;font-size:9px;max-height:160px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;line-height:1.3;color:#7d8590}
.md{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:100;align-items:center;justify-content:center;padding:16px;backdrop-filter:blur(6px)}
.md.sh{display:flex}
.mb{background:#0d1117;border:1px solid rgba(163,113,247,.15);border-radius:12px;padding:14px;width:100%;max-width:500px;max-height:80vh;overflow-y:auto}
.mb h3{font-size:14px;margin-bottom:6px;color:#a371f7}
.mb pre{font-size:10px;font-family:monospace;white-space:pre-wrap;color:#8b949e;margin:6px 0;max-height:55vh;overflow-y:auto}
.mc{background:rgba(163,113,247,.08);border:1px solid rgba(163,113,247,.2);border-radius:8px;color:#a371f7;padding:10px;width:100%;font-size:13px;cursor:pointer;margin-top:6px;font-weight:600}
</style>
</head>
<body>
<div class="banner">
 <h1>⚔ POLYBOT ⚔</h1>
 <div class="quote"><b>« Mi magia es no rendirme nunca »</b></div>
</div>
<div class="wrap">
<div class="hd"><span></span><span class="tm"><span class="dot" id="dot"></span><span id="ts">...</span></span></div>
<div id="sb" class="sb sb-off">...</div>
<div class="g2" id="cards"></div>
<h2>⚔ Control</h2>
<div class="cr">
 <button class="bt b1" onclick="act('start247')" id="b-start247">🚀 24/7</button>
 <button class="bt b2" onclick="act('start18')" id="b-start18">⏰ →18:00</button>
 <button class="bt b3" onclick="act('stop')" id="b-stop">⏹ Stop</button>
</div>
<div class="cr2">
 <button class="bt b4" onclick="act('redeem')" id="b-redeem">💰 Cobrar</button>
 <button class="bt b5" onclick="act('health')" id="b-health">🏥 Health</button>
</div>
<div class="cr2">
 <button class="bt b5" onclick="act('sync')" id="b-sync" style="border-color:rgba(63,185,80,.3)">📊 Sync Resultados</button>
 <div class="c" style="padding:6px 10px;text-align:center"><div class="l">Operando desde</div><div id="since" style="font-size:13px;font-weight:600;color:#a371f7">...</div></div>
</div>
<h2>⚔ Estrategias</h2>
<div id="st"></div>
<h2 id="ph">⚔ Posiciones</h2>
<div id="pos"></div>
<h2>⚔ Log</h2>
<div class="lb" id="lg">...</div>
</div>
<div class="md" id="md"><div class="mb"><h3 id="mt">...</h3><pre id="mo">...</pre><button class="mc" onclick="cm()">Cerrar</button></div></div>
<script>
const E={IA:'\u{1F9E0}',CRYPTO:'\u{20BF}',HARVEST:'\u{1F33E}',WEATHER:'\u{26C5}',STOCKS:'\u{1F4C8}',FLASH_CRASH:'\u{26A1}'};
const C={IA:'#58a6ff',CRYPTO:'#3fb950',HARVEST:'#d29922',WEATHER:'#a371f7',STOCKS:'#f0883e',FLASH_CRASH:'#f85149'};
const S=['s0','s1','s2','s3','s4','s5'];
const N={IA:'IA Value Bets',CRYPTO:'Crypto 15-Min',HARVEST:'NO Harvester',WEATHER:'Weather Trader',STOCKS:'Stock Market',FLASH_CRASH:'Flash Crash'};
const K=['IA','CRYPTO','HARVEST','WEATHER','STOCKS','FLASH_CRASH'];
async function rf(){
 try{
  const r=await fetch('/api/data'),d=await r.json();
  document.getElementById('ts').textContent=d.ts;
  const dot=document.getElementById('dot'),sb=document.getElementById('sb');
  if(d.bot.running){dot.className='dot don';sb.className='sb sb-on';sb.innerHTML=`\u{1F7E2} Corriendo (${d.bot.mode||'?'}) PID:${d.bot.pid||'?'}`}
  else{dot.className='dot doff';sb.className='sb sb-off';sb.innerHTML='\u{1F534} Bot detenido'}
  const b=d.summary.bankroll_actual||0;const tt=b+(d.pos_total||0);
  document.getElementById('cards').innerHTML=`
   <div class="c"><div class="l">Balance libre</div><div class="v bl">$${b.toFixed(2)}</div></div>
   <div class="c"><div class="l">En posiciones</div><div class="v yl">$${(d.pos_total||0).toFixed(2)}</div></div>
   <div class="c"><div class="l">Total estimado</div><div class="v pp">$${tt.toFixed(2)}</div></div>
   <div class="c"><div class="l">Win Rate (${d.stats.won}W/${d.stats.lost}L)</div><div class="v ${d.stats.wr>=55?'gr':d.stats.wr>0?'yl':'rd'}">${d.stats.wr}%</div></div>`;
  let s='';
  K.forEach((n,i)=>{const v=d.strategies[n]||{won:0,lost:0,pending:0,profit:0};
   s+=`<div class="sr ${S[i]}"><div class="n">${E[n]||''} ${N[n]}</div><div class="d" style="color:${C[n]}">${v.won}W / ${v.lost}L<br><span style="opacity:.5;font-size:10px">$${v.profit>=0?'+':''}${v.profit.toFixed(2)}</span></div></div>`});
  document.getElementById('st').innerHTML=s;
  let ph='';const pp=d.positions||[];
  document.getElementById('ph').textContent=`\u{2694} Posiciones (${pp.length})`;
  for(const p of pp){const c=p.pnl>=0?'gr':'rd';const sg=p.pnl>=0?'+':'';
   const rt=p.resolve?` <span style="font-size:9px;padding:1px 4px;border-radius:4px;background:rgba(163,113,247,.15);color:#a371f7">${p.resolve}</span>`:'';
   ph+=`<div class="pos"><div class="pt">${p.side} \u{2022} ${p.title}${rt}</div><div class="pv"><span class="${c}">$${p.value.toFixed(2)}</span> <span style="font-size:9px;color:#8b949e">${sg}${p.pnl.toFixed(2)}</span></div></div>`}
  document.getElementById('pos').innerHTML=ph||'<div style="color:#30363d;font-size:11px;padding:6px">Sin posiciones</div>';
  const l=document.getElementById('lg');l.textContent=d.logs||'...';l.scrollTop=l.scrollHeight;
  document.getElementById('since').textContent=(d.since||'?')+' ('+( d.days||0)+' d\u{00ED}as)';
  document.getElementById('b-start247').disabled=d.bot.running;
  document.getElementById('b-start18').disabled=d.bot.running;
  document.getElementById('b-stop').disabled=!d.bot.running;
 }catch(e){}}
async function act(a){
 const b=document.getElementById('b-'+a);if(!b)return;
 const T={start247:'Iniciar Bot 24/7',start18:'Iniciar Bot \u{2192}18:00',stop:'Detener Bot',redeem:'Cobrar Posiciones',health:'Health Check',sync:'Sync Resultados'};
 if(a==='stop'&&!confirm('\u{00BF}Detener el bot?'))return;
 b.disabled=true;
 document.getElementById('mt').textContent=T[a]||a;
 document.getElementById('mo').textContent='Ejecutando...';
 document.getElementById('md').classList.add('sh');
 try{const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:a})});
  const d=await r.json();document.getElementById('mo').textContent=(d.output||'').replace(/\u001b\[[\d;]*m/g,'').replace(/\u2190\[[\d;]*m/g,'');
  setTimeout(rf,2000)}catch(e){document.getElementById('mo').textContent='Error: '+e.message}
 setTimeout(()=>{b.disabled=false},2000)}
function cm(){document.getElementById('md').classList.remove('sh')}
rf();setInterval(rf,15000);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path in ("/","/index.html"):
                self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.end_headers()
                self.wfile.write(HTML.encode("utf-8"))
            elif self.path=="/api/data":
                data = build_data()
                self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
                self.wfile.write(json.dumps(data,default=str).encode("utf-8"))
            else:self.send_response(404);self.end_headers()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            pass
    def do_POST(self):
      try:
        if self.path=="/api/action":
            ln=int(self.headers.get("Content-Length",0))
            body=json.loads(self.rfile.read(ln)) if ln else {}
            a=body.get("action","")
            if a=="start247":out=start_bot("24/7")
            elif a=="start18":out=start_bot("stop-at-18")
            elif a=="stop":out=stop_bot()
            elif a in("redeem","health","sync"):
                cmd={"redeem":"redeem.py","health":"health_check.py","sync":"sync_results.py"}[a]
                fp=os.path.join(BOT_DIR,cmd)
                if os.path.exists(fp):
                    try:r=subprocess.run([sys.executable,cmd],capture_output=True,text=True,timeout=120,cwd=BOT_DIR,encoding='utf-8',errors='replace');out={"success":True,"output":r.stdout+r.stderr}
                    except Exception as e:out={"success":False,"output":str(e)}
                else:out={"success":False,"output":f"{cmd} no encontrado"}
            else:out={"success":False,"output":"?"}
            self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
            self.wfile.write(json.dumps(out).encode("utf-8"))
        else:self.send_response(404);self.end_headers()
      except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
          pass
      except Exception:
          pass
    def log_message(self,*a):pass


class SilentServer(HTTPServer):
    """HTTPServer that doesn't spam connection errors to terminal."""
    def handle_error(self, request, client_address):
        pass  # Silencia ConnectionReset/Aborted del celular


def main():
    try:s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(("8.8.8.8",80));ip=s.getsockname()[0];s.close()
    except:ip="127.0.0.1"
    print(f"\n{'='*50}\n  POLYBOT Dashboard v5\n  Mi magia es no rendirme nunca\n{'='*50}\n\n  PC:  http://localhost:{PORT}\n  Cel: http://{ip}:{PORT}\n\n  Ctrl+C para cerrar\n")
    sv=SilentServer(("0.0.0.0",PORT),Handler)
    try:sv.serve_forever()
    except KeyboardInterrupt:print("\nDashboard cerrado");sv.shutdown()

if __name__=="__main__":main()
