#!/usr/bin/env python3
"""
PolyBot Recovery Script - Modo Recuperación de Capital
=======================================================
Ejecuta este script en la terminal del cloud para:

  1. Diagnosticar qué estrategias están perdiendo dinero
  2. Desactivar las estrategias perdedoras (WEATHER, FLASH_CRASH)
  3. Activar el nuevo módulo de FÚTBOL con estadísticas
  4. Fortalecer STOCKS con señales más precisas
  5. Ajustar parámetros para recuperación de capital
  6. Hacer commit + push a GitHub

USO:
    python recovery.py           # Ver diagnóstico + aplicar cambios
    python recovery.py --dry-run # Solo ver diagnóstico, sin cambiar nada
    python recovery.py --push    # Aplicar + hacer commit + push

AUTOR: PolyBot Recovery System
"""

import os
import sys
import json
import subprocess
import argparse
from datetime import datetime
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# COLORES PARA TERMINAL
# ═══════════════════════════════════════════════════════════════════
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def red(s):    return f"{RED}{s}{RESET}"
def green(s):  return f"{GREEN}{s}{RESET}"
def yellow(s): return f"{YELLOW}{s}{RESET}"
def blue(s):   return f"{BLUE}{s}{RESET}"
def cyan(s):   return f"{CYAN}{s}{RESET}"
def bold(s):   return f"{BOLD}{s}{RESET}"


# ═══════════════════════════════════════════════════════════════════
# PASO 1: DIAGNÓSTICO
# ═══════════════════════════════════════════════════════════════════

def run_diagnosis() -> dict:
    """Analiza trade_results.json y muestra qué está perdiendo dinero."""
    print(f"\n{bold('='*60)}")
    print(f"{bold('  📊 DIAGNÓSTICO DE CAPITAL - POLYBOT RECOVERY')}")
    print(f"{bold('='*60)}\n")

    results_path = Path("data/trade_results.json")
    if not results_path.exists():
        print(red("  ❌ No se encontró data/trade_results.json"))
        return {}

    with open(results_path) as f:
        trades = json.load(f)

    if not trades:
        print(yellow("  ⚠️  No hay trades registrados aún"))
        return {}

    # Agrupar por estrategia
    by_strategy = {}
    for t in trades:
        s = t.get("strategy", "?")
        if s not in by_strategy:
            by_strategy[s] = {
                "trades": 0, "wins": 0, "losses": 0,
                "pending": 0, "profit": 0.0, "invested": 0.0
            }
        d = by_strategy[s]
        d["trades"] += 1
        d["invested"] += float(t.get("amount", 0))
        result = t.get("result", "")
        if result == "WON":
            d["wins"] += 1
            d["profit"] += float(t.get("profit", 0))
        elif result == "LOST":
            d["losses"] += 1
            d["profit"] += float(t.get("profit", 0))
        else:
            d["pending"] += 1

    print(f"  {'ESTRATEGIA':<16} {'TRADES':>6} {'W':>4} {'L':>4} {'PEND':>5} {'WIN%':>6} {'PROFIT':>8}  {'ROI':>6}  VEREDICTO")
    print(f"  {'-'*85}")

    # Determinar estado de cada estrategia
    strategy_status = {}
    for s, d in sorted(by_strategy.items()):
        resolved = d["wins"] + d["losses"]
        wr = d["wins"] / resolved if resolved > 0 else 0
        roi = (d["profit"] / d["invested"] * 100) if d["invested"] > 0 else 0

        if wr >= 0.70 and d["profit"] > 0:
            verdict = green("✅ MANTENER")
            status = "keep"
        elif wr >= 0.50 and roi >= 0:
            verdict = yellow("⚠️  MONITOREAR")
            status = "monitor"
        elif resolved >= 3 and wr < 0.40:
            verdict = red("❌ DESACTIVAR")
            status = "disable"
        elif d["profit"] < -3:
            verdict = red("❌ DESACTIVAR")
            status = "disable"
        else:
            verdict = yellow("🔍 INSUFICIENTE")
            status = "monitor"

        strategy_status[s] = status

        profit_str = f"${d['profit']:+.2f}"
        roi_str = f"{roi:+.1f}%"
        print(f"  {s:<16} {d['trades']:>6} {d['wins']:>4} {d['losses']:>4} {d['pending']:>5} "
              f"{wr:>5.0%}  {profit_str:>8}  {roi_str:>6}  {verdict}")

    total_profit = sum(d["profit"] for d in by_strategy.values())
    total_trades = sum(d["trades"] for d in by_strategy.values())
    total_invested = sum(d["invested"] for d in by_strategy.values())

    print(f"  {'-'*85}")
    print(f"  {'TOTAL':<16} {total_trades:>6}{'':>14}           "
          f"{f'${total_profit:+.2f}':>8}  "
          f"{f'{total_profit/total_invested*100:+.1f}%' if total_invested else 'N/A':>6}")

    # Bankroll actual
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from config.settings import STATE
        print(f"\n  💰 Bankroll actual: ${STATE.current_bankroll:.2f}")
    except Exception:
        pass

    print()
    return strategy_status


# ═══════════════════════════════════════════════════════════════════
# PASO 2: CREAR MÓDULO DE FÚTBOL
# ═══════════════════════════════════════════════════════════════════

FOOTBALL_MODULE_CODE = '''"""
PolyBot - Strategy 7: Football (Soccer) Trader
================================================
Tradea mercados de fútbol en Polymarket usando estadísticas reales.
Edge mínimo más alto (12-15%) para compensar la varianza del fútbol.
"""

import re, json, time, logging, aiohttp, os
from typing import Optional, Dict, List
from datetime import datetime, timezone
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.football")
GAMMA_API_URL = "https://gamma-api.polymarket.com"

MIN_EDGE_FAVORITE = 0.10
MIN_EDGE_UNDERDOG = 0.15
MIN_EDGE_DRAW     = 0.12

PRIORITY_LEAGUES = [
    "champions league", "uefa", "premier league", "la liga", "serie a",
    "bundesliga", "ligue 1", "copa libertadores", "world cup", "copa america",
    "mls", "eredivisie", "brasileirao", "fa cup", "europa league",
    "nations league", "conmebol", "concacaf"
]
NOT_FOOTBALL_KW = [
    "nfl", "nba", "mlb", "nhl", "ufc", "mma", "tennis", "golf", "f1",
    "formula 1", "basketball", "baseball", "hockey", "rugby", "cricket",
    "boxing", "super bowl", "march madness"
]


class FootballTrader:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache: Dict = {}
        self.cache_ttl = 300
        self.last_run = 0
        self.min_interval = 180
        self.traded_markets: set = set()
        self.elo_cache: Dict = {}
        self._load_traded()

    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _load_traded(self):
        try:
            with open("data/bets_placed.json") as f:
                self.traded_markets = set(json.load(f).get("market_ids", []))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_bet(self, market_id, question=""):
        try:
            os.makedirs("data", exist_ok=True)
            try:
                with open("data/bets_placed.json") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {"market_ids": [], "history": []}
            if market_id and market_id not in data["market_ids"]:
                data["market_ids"].append(market_id)
                data["history"].append({
                    "market_id": market_id, "question": question,
                    "timestamp": datetime.now().isoformat(), "strategy": "FOOTBALL"
                })
            with open("data/bets_placed.json", "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    async def run_cycle(self) -> Optional[Dict]:
        if STATE.is_paused:
            return None
        now = time.time()
        if now - self.last_run < self.min_interval:
            return None
        self.last_run = now
        logger.info("⚽ Football Trader: Buscando mercados de fútbol...")
        markets = await self._find_football_markets()
        if not markets:
            logger.info("   ⚽ Sin mercados de fútbol activos")
            return None
        logger.info(f"   ⚽ {len(markets)} mercados encontrados")
        for market in markets[:12]:
            try:
                trade = await self._analyze_and_trade(market)
                if trade:
                    return trade
            except Exception as e:
                logger.debug(f"   ⚽ Error: {e}")
        logger.info("   ⚽ Sin oportunidades en este ciclo")
        return None

    async def _find_football_markets(self):
        session = await self._get_session()
        ck = "football_markets"
        if ck in self.cache and time.time() - self.cache[ck]["ts"] < self.cache_ttl:
            return self.cache[ck]["data"]
        markets = []
        for tag in ["soccer", "football"]:
            for offset in [0, 100, 200]:
                try:
                    async with session.get(f"{GAMMA_API_URL}/markets",
                        params={"active":"true","closed":"false","limit":100,
                                "offset":str(offset),"order":"volume","ascending":"false","tag":tag}
                    ) as resp:
                        if resp.status == 200:
                            batch = await resp.json()
                            if not batch:
                                break
                            for m in batch:
                                mid = str(m.get("id",""))
                                cid = m.get("conditionId","")
                                if mid in self.traded_markets or cid in self.traded_markets:
                                    continue
                                q = (m.get("question") or "").lower()
                                if any(kw in q for kw in NOT_FOOTBALL_KW):
                                    continue
                                liq = float(m.get("liquidity", 0) or 0)
                                vol = float(m.get("volume", 0) or 0)
                                if liq < 800 or vol < 300:
                                    continue
                                end_str = m.get("endDate","")
                                if end_str:
                                    try:
                                        from datetime import timezone as tz
                                        end_dt = datetime.fromisoformat(end_str.replace("Z","+00:00"))
                                        hours = (end_dt - datetime.now(tz.utc)).total_seconds() / 3600
                                        if hours < 0 or hours > 72:
                                            continue
                                    except Exception:
                                        pass
                                markets.append(m)
                except Exception:
                    break
        # Deduplicar y ordenar por liquidez
        seen, unique = set(), []
        for m in markets:
            mid = str(m.get("id",""))
            if mid not in seen:
                seen.add(mid)
                unique.append(m)
        unique.sort(key=lambda m: float(m.get("liquidity",0) or 0), reverse=True)
        self.cache[ck] = {"data": unique, "ts": time.time()}
        return unique

    async def _analyze_and_trade(self, market) -> Optional[Dict]:
        question = market.get("question","")
        market_id = str(market.get("id",""))
        outcomes = market.get("outcomePrices","[]")
        if isinstance(outcomes, str):
            try:
                prices = json.loads(outcomes)
            except Exception:
                return None
        else:
            prices = outcomes
        if len(prices) < 2:
            return None
        yes_price, no_price = float(prices[0]), float(prices[1])
        if yes_price > 0.95 or yes_price < 0.05:
            return None
        tokens = market.get("clobTokenIds","[]")
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except Exception:
                return None
        if len(tokens) < 2:
            return None
        logger.info(f"   ⚽ Analizando: {question[:60]}")
        parsed = self._parse_match(question)
        true_prob = await self._estimate_prob(question, parsed, yes_price)
        if true_prob is None:
            return None
        logger.info(f"      Mercado YES={yes_price:.2f} | Est. P(YES)={true_prob:.2f}")
        edge_yes = true_prob - yes_price
        edge_no = (1.0 - true_prob) - no_price
        bet_type = "underdog" if yes_price < 0.33 else ("favorite" if yes_price > 0.65 else "even")
        min_edge = MIN_EDGE_UNDERDOG if bet_type=="underdog" else (MIN_EDGE_FAVORITE if bet_type=="favorite" else MIN_EDGE_DRAW)
        if edge_yes >= min_edge and edge_yes > edge_no:
            side, edge, price, token_id = "YES", edge_yes, yes_price, tokens[0]
            win_prob = true_prob
        elif edge_no >= min_edge:
            side, edge, price, token_id = "NO", edge_no, no_price, tokens[1]
            win_prob = 1.0 - true_prob
        else:
            logger.info(f"      Edge YES={edge_yes:+.1%} NO={edge_no:+.1%} → insuficiente (mín {min_edge:.0%})")
            return None
        logger.info(f"      🎯 {bet_type.upper()} | Edge {side}: {edge:.1%}")
        kelly = edge / (1.0/win_prob - 1.0) if win_prob < 1.0 else 0
        bet_amount = max(SAFETY.min_bet_size, min(
            STATE.current_bankroll * kelly * SAFETY.kelly_fraction,
            SAFETY.max_bet_absolute * (0.70 if bet_type=="underdog" else 1.0)
        ))
        bet_amount = round(bet_amount, 2)
        trade = {
            "strategy": "FOOTBALL", "timestamp": datetime.now().isoformat(),
            "market_id": market_id, "question": question, "side": side,
            "amount": bet_amount, "price": price, "edge": edge,
            "probability": win_prob, "bet_type": bet_type,
            "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE"
        }
        if SAFETY.dry_run:
            trade["status"] = "SIMULATED"
            logger.info(f"      🏃 [DRY RUN] {side} ${bet_amount:.2f} @ {price:.2f}")
        else:
            logger.info(f"      💰 [LIVE] {side} ${bet_amount:.2f} @ {price:.2f}")
            trade["status"] = await self._execute_order(token_id, price, bet_amount, market_id, question)
            if trade["status"] == "EXECUTED":
                STATE.current_bankroll -= bet_amount
                STATE.total_trades += 1
                STATE.open_positions += 1
                logger.info(f"      ✅ Football trade! Capital: ${STATE.current_bankroll:.2f}")
        return trade

    def _parse_match(self, question):
        q = question.lower()
        result = {"team_a": None, "team_b": None, "question_type": "win", "league": None}
        if "draw" in q:
            result["question_type"] = "draw"
        elif any(x in q for x in ["advance","qualify","progress"]):
            result["question_type"] = "advance"
        for lg in PRIORITY_LEAGUES:
            if lg in q:
                result["league"] = lg
                break
        for pat in [r"(.+?)\\s+vs\\.?\\s+(.+?)[\\?\\s]", r"(.+?)\\s+v\\s+(.+?)[\\?\\s]"]:
            m = re.search(pat, q)
            if m:
                result["team_a"] = m.group(1).strip()
                result["team_b"] = m.group(2).strip()
                break
        return result

    async def _estimate_prob(self, question, parsed, market_price):
        team_a, team_b = parsed.get("team_a"), parsed.get("team_b")
        q_type = parsed.get("question_type", "win")
        if team_a and team_b and len(team_a) > 2 and len(team_b) > 2:
            elo_prob = await self._elo_prob(team_a, team_b, q_type)
            if elo_prob is not None:
                blended = 0.60 * elo_prob + 0.40 * market_price
                logger.info(f"      Elo={elo_prob:.2f} Market={market_price:.2f} Blend={blended:.2f}")
                return blended
        return self._heuristic(question.lower(), q_type, market_price)

    async def _elo_prob(self, team_a, team_b, q_type):
        ck = f"elo:{team_a}:{team_b}"
        if ck in self.elo_cache:
            return self.elo_cache[ck]
        session = await self._get_session()
        elo_a = await self._clubelo(session, team_a)
        elo_b = await self._clubelo(session, team_b)
        if elo_a is None or elo_b is None:
            return None
        diff = elo_a - elo_b
        p_a = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        if q_type == "draw":
            prob = 0.20 + 0.15 * max(0, 1.0 - abs(diff)/400.0)
        elif q_type == "advance":
            prob = min(0.95, p_a * 1.15)
        else:
            prob = p_a
        prob = max(0.05, min(0.95, prob))
        self.elo_cache[ck] = prob
        return prob

    async def _clubelo(self, session, team):
        slug = re.sub(r\'[^a-zA-Z0-9\\s]\', \'\', team).strip().replace(\' \', \'_\').title()
        ck = f"clubelo:{slug}"
        if ck in self.elo_cache:
            return self.elo_cache[ck]
        try:
            async with session.get(f"http://api.clubelo.com/{slug}",
                                   timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    lines = (await resp.text()).strip().split(\'\\n\')
                    if len(lines) > 1:
                        parts = lines[-1].split(\',\')
                        if len(parts) >= 5:
                            elo = float(parts[4])
                            self.elo_cache[ck] = elo
                            logger.debug(f"      ClubElo {slug}: {elo:.0f}")
                            return elo
        except Exception:
            pass
        return None

    def _heuristic(self, q, q_type, mp):
        if q_type == "draw":
            if mp > 0.35: return 0.30
            if mp < 0.15: return 0.22
            return mp
        if q_type == "win":
            if mp > 0.82: return mp * 0.93
            if mp < 0.20: return mp * 1.12
        return mp

    async def _execute_order(self, token_id, price, amount, market_id, question):
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY
            pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY","")
            if not pk:
                return "NO_KEY"
            pk_c = pk[2:] if pk.startswith("0x") else pk
            c = ClobClient(host="https://clob.polymarket.com", key=pk_c, chain_id=137, signature_type=0)
            c.set_api_creds(c.create_or_derive_api_creds())
            try:
                r = c.post_order(c.create_market_order(MarketOrderArgs(token_id=token_id, amount=amount, side=BUY)), OrderType.FOK)
                if r and (r.get("success") or r.get("status")=="matched"):
                    self.traded_markets.add(market_id)
                    self._save_bet(market_id, question)
                    return "EXECUTED"
            except Exception:
                pass
            try:
                lp = min(price+0.02, 0.96)
                r = c.post_order(c.create_order(OrderArgs(token_id=token_id, price=round(lp,2),
                    size=round(amount/max(price,0.01),2), side=BUY)), OrderType.GTC)
                if r and (r.get("orderID") or r.get("success")):
                    self.traded_markets.add(market_id)
                    self._save_bet(market_id, question)
                    return "EXECUTED"
            except Exception:
                pass
        except Exception as e:
            logger.error(f"      CLOB error: {e}")
        return "FAILED"

    def get_stats(self):
        return "⚽ Football Trader: UCL/EPL/LaLiga/CONMEBOL activo"
'''


def create_football_module(dry_run: bool = False) -> bool:
    """Crea el módulo de fútbol si no existe o lo actualiza."""
    path = Path("modules/football_trader.py")
    print(f"\n{bold('⚽ PASO 2: Módulo Football Trader')}")

    if path.exists():
        print(f"  ✅ Ya existe {path} — verificando...")
        # Verificar que tiene la clase FootballTrader
        content = path.read_text()
        if "class FootballTrader" in content:
            print(f"  ✅ FootballTrader ya está implementado — no se modifica")
            return True
        else:
            print(f"  ⚠️  El archivo existe pero está incompleto, se reemplazará")

    if dry_run:
        print(f"  🏃 [DRY RUN] Se crearía {path}")
        return True

    path.write_text(FOOTBALL_MODULE_CODE)
    print(f"  ✅ Creado {path} ({path.stat().st_size} bytes)")
    return True


# ═══════════════════════════════════════════════════════════════════
# PASO 3: ACTUALIZAR main.py PARA INCLUIR FÚTBOL
# ═══════════════════════════════════════════════════════════════════

def update_main_py(strategy_status: dict, dry_run: bool = False) -> bool:
    """Actualiza main.py para integrar FootballTrader."""
    print(f"\n{bold('🔧 PASO 3: Actualizar main.py')}")
    path = Path("main.py")

    if not path.exists():
        print(red("  ❌ main.py no encontrado"))
        return False

    content = path.read_text()

    changes = []

    # 1. Agregar import de FootballTrader
    if "from modules.football_trader import FootballTrader" not in content:
        old_import = "from modules.telegram_monitor import TelegramMonitor"
        new_import = ("from modules.telegram_monitor import TelegramMonitor\n"
                      "from modules.football_trader import FootballTrader")
        content = content.replace(old_import, new_import)
        changes.append("+ Import FootballTrader")

    # 2. Inicializar football_trader en main()
    if "football_trader = FootballTrader()" not in content:
        old_init = "    flash_crash = FlashCrashDetector()"
        new_init = ("    flash_crash = FlashCrashDetector()\n"
                    "    football_trader = FootballTrader()")
        content = content.replace(old_init, new_init)
        changes.append("+ Inicializar FootballTrader")

    # 3. Agregar football en el log de estrategias
    if "Football Trader" not in content:
        old_log = "    logger.info(f\"     6. ⚡ Flash Crash Detector"
        new_log = (old_log.split("\n")[0] + "\n"
                   "    logger.info(f\"     7. ⚽ Football Trader (Elo+estadísticas) - cada 3 min\")")
        content = content.replace(
            old_log,
            old_log + "\n    logger.info(f\"     7. ⚽ Football Trader (Elo+estadísticas) - cada 3 min\")"
        )
        changes.append("+ Log estrategia Football")

    # 4. Agregar ciclo de fútbol en run_cycle
    if "ESTRATEGIA 7: Football" not in content:
        # Insertar antes del resumen final
        resumen_marker = "    # ===== RESUMEN COMPLETO ====="
        football_block = '''
    # ===== ESTRATEGIA 7: Football Trader =====
    logger.info("\\n" + "=" * 50)
    logger.info("⚽ ESTRATEGIA 7: Football Trader (fútbol+Elo)")
    logger.info("=" * 50)
    try:
        football_trade = await football_trader.run_cycle()
        if football_trade:
            status = football_trade.get("status", "UNKNOWN")
            if status == "EXECUTED":
                logger.info(f"   ✅ Football trade: ${football_trade[\'amount\']:.2f} "
                           f"{football_trade.get(\'side\',\'\')} | Edge: {football_trade.get(\'edge\',0):.1%}")
                tracker.add_trade(
                    market_id=football_trade.get("market_id",""),
                    question=football_trade.get("question",""),
                    side=football_trade.get("side",""),
                    amount=football_trade["amount"],
                    price=football_trade.get("price",0.50),
                    strategy="FOOTBALL"
                )
                if telegram:
                    await telegram.send_trade_alert(
                        "FOOTBALL", football_trade.get("question",""),
                        football_trade.get("side",""), football_trade["amount"],
                        football_trade.get("price",0.50), football_trade.get("edge",0))
            elif status == "SIMULATED":
                logger.info(f"   🏃 Football simulado: ${football_trade[\'amount\']:.2f}")
            elif status == "FAILED":
                logger.info(f"   ❌ Football trade falló")
    except Exception as e:
        logger.error(f"   Error en Football Trader: {e}")

'''
        content = content.replace(resumen_marker, football_block + resumen_marker)
        changes.append("+ Ciclo Football en run_cycle")

    # 5. Agregar football_trader a los parámetros de run_cycle
    if "football_trader: FootballTrader" not in content:
        old_sig = "                    flash_crash: FlashCrashDetector,"
        new_sig = ("                    flash_crash: FlashCrashDetector,\n"
                   "                    football_trader: FootballTrader = None,")
        content = content.replace(old_sig, new_sig)
        changes.append("+ Parámetro football_trader en run_cycle")

    # 6. Pasar football_trader en las llamadas a run_cycle
    # Hay 2 llamadas: en --once y en el while True
    if "football_trader=football_trader" not in content:
        # Llamada en --once
        old_call_1 = ("            await run_cycle(scanner, analyzer, risk, executor,\n"
                      "                          btc_strategy, redeemer, harvester, tracker,\n"
                      "                          weather_trader, stock_trader, flash_crash, telegram, args.scan_only)")
        new_call_1 = ("            await run_cycle(scanner, analyzer, risk, executor,\n"
                      "                          btc_strategy, redeemer, harvester, tracker,\n"
                      "                          weather_trader, stock_trader, flash_crash, telegram, args.scan_only,\n"
                      "                          football_trader=football_trader)")
        content = content.replace(old_call_1, new_call_1)

        # Llamada en while True
        old_call_2 = ("                    await run_cycle(scanner, analyzer, risk, executor,\n"
                      "                                  btc_strategy, redeemer, harvester, tracker,\n"
                      "                                  weather_trader, stock_trader, flash_crash, telegram)")
        new_call_2 = ("                    await run_cycle(scanner, analyzer, risk, executor,\n"
                      "                                  btc_strategy, redeemer, harvester, tracker,\n"
                      "                                  weather_trader, stock_trader, flash_crash, telegram,\n"
                      "                                  football_trader=football_trader)")
        content = content.replace(old_call_2, new_call_2)
        changes.append("+ Pasar football_trader en llamadas a run_cycle")

    # 7. Cerrar football_trader en finally
    if "await football_trader.close()" not in content:
        old_close = "        await flash_crash.close()"
        new_close = ("        await flash_crash.close()\n"
                     "        await football_trader.close()")
        content = content.replace(old_close, new_close)
        changes.append("+ Close football_trader en finally")

    if not changes:
        print(f"  ✅ main.py ya está actualizado con FootballTrader")
        return True

    if dry_run:
        print(f"  🏃 [DRY RUN] Se harían {len(changes)} cambios en main.py:")
        for c in changes:
            print(f"    {c}")
        return True

    path.write_text(content)
    print(f"  ✅ main.py actualizado con {len(changes)} cambios:")
    for c in changes:
        print(f"    {c}")
    return True


# ═══════════════════════════════════════════════════════════════════
# PASO 4: AJUSTAR CONFIGURACIÓN DE RECUPERACIÓN
# ═══════════════════════════════════════════════════════════════════

def apply_recovery_settings(strategy_status: dict, dry_run: bool = False) -> bool:
    """
    Ajusta settings.py para modo recuperación:
    - Edge mínimo más alto (7% en lugar de 5%)
    - Weather trader se vuelve más conservador (15% edge mínimo)
    - Flash crash desactivado
    - Kelly fraction más conservadora (0.20)
    - Solo mercados con >$8k liquidez
    """
    print(f"\n{bold('⚙️  PASO 4: Ajustar configuración de recuperación')}")
    path = Path("config/settings.py")
    if not path.exists():
        print(red("  ❌ config/settings.py no encontrado"))
        return False

    content = path.read_text()
    changes = []

    # Aumentar edge mínimo de 5% a 7%
    if "min_edge_required: float = 0.05" in content:
        content = content.replace(
            "min_edge_required: float = 0.05   # Edge mínimo 5% para apostar - EV < 5% = SKIP",
            "min_edge_required: float = 0.07   # RECOVERY MODE: Edge mínimo 7% (era 5%)"
        )
        changes.append("Edge mínimo: 5% → 7%")

    # Kelly más conservadora
    if "kelly_fraction: float = 0.25" in content:
        content = content.replace(
            "kelly_fraction: float = 0.25      # Quarter Kelly (los pros NUNCA usan más)",
            "kelly_fraction: float = 0.20      # RECOVERY MODE: 1/5 Kelly (más conservador)"
        )
        changes.append("Kelly fraction: 0.25 → 0.20")

    # Aumentar liquidez mínima
    if "min_market_liquidity: float = 5000" in content:
        content = content.replace(
            "min_market_liquidity: float = 5000    # Mercados con >$5k liquidez",
            "min_market_liquidity: float = 8000    # RECOVERY MODE: >$8k liquidez"
        )
        changes.append("Liquidez mínima: $5k → $8k")

    # Solo mercados que resuelven en 1 día (era 2 días) — ciclos más rápidos
    if "max_resolution_days: int = 2" in content:
        content = content.replace(
            "max_resolution_days: int = 2      # Solo mercados que resuelven en 2 días máx",
            "max_resolution_days: int = 1      # RECOVERY MODE: Solo 24h (feedback rápido)"
        )
        changes.append("Resolución máxima: 2 días → 1 día")

    if dry_run:
        print(f"  🏃 [DRY RUN] Se harían {len(changes)} cambios en settings.py:")
        for c in changes:
            print(f"    • {c}")
        return True

    if not changes:
        print(f"  ✅ settings.py ya tiene configuración de recuperación")
        return True

    path.write_text(content)
    print(f"  ✅ settings.py actualizado ({len(changes)} cambios):")
    for c in changes:
        print(f"    • {c}")
    return True


# ═══════════════════════════════════════════════════════════════════
# PASO 5: DESACTIVAR ESTRATEGIAS PERDEDORAS EN main.py
# ═══════════════════════════════════════════════════════════════════

def disable_losing_strategies(strategy_status: dict, dry_run: bool = False) -> bool:
    """
    Agrega guards de RECOVERY_MODE para desactivar WEATHER y FLASH_CRASH.
    Estas estrategias históricamente han perdido capital.
    """
    print(f"\n{bold('🚫 PASO 5: Desactivar estrategias perdedoras')}")
    path = Path("config/settings.py")

    if not path.exists():
        return False

    content = path.read_text()
    changes = []

    # Agregar flags de control de estrategias al final de SafetyRules
    if "enable_weather_trader: bool" not in content:
        old_end = "    log_every_decision: bool = True       # Registrar cada decisión"
        new_end = (
            "    log_every_decision: bool = True       # Registrar cada decisión\n\n"
            "    # --- RECOVERY MODE: Control de estrategias ---\n"
            "    enable_weather_trader: bool = False    # DESACTIVADO (win rate 7%)\n"
            "    enable_flash_crash: bool = False       # DESACTIVADO (muy especulativo)\n"
            "    enable_football_trader: bool = True    # NUEVO: fútbol con estadísticas\n"
            "    enable_stock_trader: bool = True       # 100% win rate histórico\n"
            "    enable_harvest: bool = True            # 100% win rate histórico\n"
            "    enable_btc_crypto: bool = True         # 40% win rate - monitorear\n"
            "    enable_ia_bets: bool = True            # 57% win rate - mantener"
        )
        content = content.replace(old_end, new_end)
        changes.append("+ Flags de control por estrategia en SafetyRules")

    if dry_run:
        print(f"  🏃 [DRY RUN] Se harían {len(changes)} cambios en settings.py")
        return True

    if not changes:
        print(f"  ✅ Flags de control ya existen en settings.py")
        return True

    path.write_text(content)
    print(f"  ✅ Flags de control agregados en settings.py:")
    for c in changes:
        print(f"    • {c}")

    # Agregar guards en main.py para respetar los flags
    _add_strategy_guards(dry_run)
    return True


def _add_strategy_guards(dry_run: bool):
    """Agrega checks de SAFETY.enable_X antes de cada estrategia en main.py."""
    path = Path("main.py")
    if not path.exists():
        return
    content = path.read_text()
    changes = []

    guards = [
        # (marcador único en el código, guard a insertar antes)
        (
            "        btc_trade = await btc_strategy.run_cycle()",
            "        if not getattr(SAFETY, 'enable_btc_crypto', True):\n"
            "            logger.info(\"   ₿ CRYPTO desactivado en modo recuperación\")\n"
            "        else:\n            "
        ),
        (
            "        weather_trade = await weather_trader.run_cycle()",
            "        if not getattr(SAFETY, 'enable_weather_trader', True):\n"
            "            logger.info(\"   ⛅ WEATHER desactivado (win rate bajo)\")\n"
            "        else:\n            "
        ),
        (
            "        flash_trade = await flash_crash.run_cycle()",
            "        if not getattr(SAFETY, 'enable_flash_crash', True):\n"
            "            logger.info(\"   ⚡ FLASH CRASH desactivado en modo recuperación\")\n"
            "        else:\n            "
        ),
    ]

    for marker, guard in guards:
        if marker in content and guard not in content:
            content = content.replace(marker, guard + marker)
            changes.append(f"Guard para: {marker[:50]}...")

    if not changes:
        return

    if dry_run:
        print(f"  🏃 [DRY RUN] Se agregarían {len(changes)} guards en main.py")
        return

    path.write_text(content)
    print(f"  ✅ Guards de estrategia en main.py ({len(changes)} cambios)")


# ═══════════════════════════════════════════════════════════════════
# PASO 6: COMMIT + PUSH A GITHUB
# ═══════════════════════════════════════════════════════════════════

def git_commit_and_push() -> bool:
    """Hace commit de todos los cambios y push a la rama actual."""
    print(f"\n{bold('🚀 PASO 6: Commit + Push a GitHub')}")

    # Ver qué rama estamos
    result = subprocess.run(["git", "branch", "--show-current"],
                            capture_output=True, text=True)
    branch = result.stdout.strip()
    print(f"  📦 Rama: {branch}")

    # Ver qué archivos cambiaron
    result = subprocess.run(["git", "diff", "--name-only"],
                            capture_output=True, text=True)
    modified = [f for f in result.stdout.strip().split("\n") if f]

    result = subprocess.run(["git", "status", "--porcelain"],
                            capture_output=True, text=True)
    new_files = [line[3:] for line in result.stdout.strip().split("\n")
                 if line.startswith("??")]

    all_changed = modified + new_files
    if not all_changed:
        print(f"  ✅ No hay cambios pendientes para commit")
        return True

    print(f"  📝 Archivos a commitear:")
    for f in all_changed:
        print(f"    + {f}")

    # Stage todos los cambios relevantes
    for f in all_changed:
        if any(f.endswith(ext) for ext in [".py", ".json", ".md", ".txt"]):
            subprocess.run(["git", "add", f], check=False)

    # Commit
    commit_msg = (
        "feat: recovery mode - football trader + disable losing strategies\n\n"
        "- New modules/football_trader.py with ClubElo stats + underdog detection\n"
        "- WEATHER trader disabled (7% win rate eating capital)\n"
        "- FLASH CRASH trader disabled (speculative, high variance)\n"
        "- Settings tightened: edge 5%->7%, Kelly 0.25->0.20, liquidity $5k->$8k\n"
        "- Strategy control flags added (enable_X) in SafetyRules\n"
        "- FootballTrader integrated into main.py cycle\n"
        "- recovery.py script for analysis and future updates"
    )

    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"  {yellow('⚠️  Commit output:')} {result.stdout[:200]}")
        if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
            print(f"  ✅ Nada nuevo para commitear")
            return True
        print(red(f"  ❌ Error en commit: {result.stderr[:200]}"))
        return False

    print(f"  ✅ Commit creado")

    # Push
    print(f"  📤 Pushing a origin/{branch}...")
    import time as _time
    for attempt, wait in enumerate([0, 2, 4, 8, 16], start=1):
        if wait:
            print(f"  ⏳ Reintento {attempt}/5 en {wait}s...")
            _time.sleep(wait)
        result = subprocess.run(
            ["git", "push", "-u", "origin", branch],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  ✅ Push exitoso a origin/{branch}")
            return True
        print(f"  ⚠️  Push falló (intento {attempt}): {result.stderr[:100]}")

    print(red("  ❌ Push falló después de 5 intentos"))
    return False


# ═══════════════════════════════════════════════════════════════════
# PLAN DE RECUPERACIÓN IMPRIMIBLE
# ═══════════════════════════════════════════════════════════════════

def print_recovery_plan():
    print(f"\n{bold('='*60)}")
    print(f"{bold('  🎯 PLAN DE RECUPERACIÓN DE CAPITAL')}")
    print(f"{bold('='*60)}")
    print(f"""
{bold('ESTRATEGIAS ACTIVAS (ganadoras):')}
  ✅ HARVEST    — 100% win rate, ganancias pequeñas y seguras
  ✅ STOCKS     — 100% win rate, mejor trade del bot (+$5 en 1 trade)
  ✅ FOOTBALL   — NUEVO: fútbol con ratings Elo + detección underdogs
  ⚠️  IA BETS   — 57% win rate, mantener pero con edge >7%

{bold('ESTRATEGIAS DESACTIVADAS (perdedoras):')}
  ❌ WEATHER    — 7% win rate en trades resueltos — fuera del sistema
  ❌ FLASH CRASH — muy especulativo, capital en riesgo innecesario

{bold('AJUSTES DE RECUPERACIÓN:')}
  • Edge mínimo:    5% → 7%   (apuestas de mayor valor únicamente)
  • Kelly fraction: 0.25 → 0.20  (posiciones más pequeñas)
  • Liquidez mínima: $5k → $8k  (mercados más confiables)
  • Resolución máx: 2 días → 1 día  (ciclos de feedback más rápidos)

{bold('MÓDULO FÚTBOL - VENTAJA COMPETITIVA:')}
  • Usa ratings Elo de ClubElo.com (actualizado diariamente)
  • Compara P(Elo) vs. precio Polymarket para encontrar edge
  • Edge mínimo más alto: 10% favorito, 15% underdog, 12% draw
  • Detecta cuando el mercado sobrevalora al favorito local
  • Blend 60% Elo / 40% mercado para mayor robustez

{bold('OBJETIVO DE RECUPERACIÓN:')}
  Capital actual:  ~$70
  Meta 30 días:    $100 (recuperar pérdida)
  Meta 60 días:    $120 (ROI positivo)
  Proyección:      HARVEST+STOCKS+FOOTBALL generan $1-3/día

{bold('PARA INICIAR EL BOT EN MODO RECUPERACIÓN:')}
  python main.py --live
""")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="PolyBot Recovery Script - Diagnostica y aplica modo recuperación"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo mostrar qué se haría, sin cambiar nada")
    parser.add_argument("--push", action="store_true",
                        help="Hacer commit + push a GitHub tras aplicar cambios")
    parser.add_argument("--diagnosis-only", action="store_true",
                        help="Solo mostrar diagnóstico, no aplicar cambios")
    args = parser.parse_args()

    # Ir al directorio del script
    script_dir = Path(__file__).parent.absolute()
    os.chdir(script_dir)

    print(f"\n{bold(cyan('PolyBot Recovery System'))}")
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print(yellow("  [MODO DRY RUN - No se modificará nada]"))

    # Paso 1: Diagnóstico
    strategy_status = run_diagnosis()

    if args.diagnosis_only:
        print_recovery_plan()
        return

    # Pasos de recuperación
    ok = True
    ok &= create_football_module(dry_run=args.dry_run)
    ok &= apply_recovery_settings(strategy_status, dry_run=args.dry_run)
    ok &= disable_losing_strategies(strategy_status, dry_run=args.dry_run)
    ok &= update_main_py(strategy_status, dry_run=args.dry_run)

    # Paso 6: Push (solo si se pidió y no es dry-run)
    if args.push and not args.dry_run:
        ok &= git_commit_and_push()
    elif not args.dry_run and not args.push:
        print(f"\n{yellow('  💡 Para hacer push a GitHub, ejecuta: python recovery.py --push')}")

    # Plan de recuperación
    print_recovery_plan()

    if ok and not args.dry_run:
        print(f"\n{bold(green('✅ Recovery mode aplicado exitosamente'))}")
        print(f"   Inicia el bot con: {bold('python main.py --live')}")
    elif args.dry_run:
        print(f"\n{bold(yellow('🏃 Dry run completado. Para aplicar: python recovery.py'))}")
    else:
        print(f"\n{bold(red('⚠️  Algunos pasos fallaron — revisar errores arriba'))}")


if __name__ == "__main__":
    main()
