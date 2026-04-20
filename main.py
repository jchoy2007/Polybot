"""
PolyBot - Bot Principal
========================
Orquesta todos los módulos: escaneo, análisis, riesgo y ejecución.
Corre en un loop continuo cada 15 minutos.

USO:
    python main.py              # Corre el bot en modo automático
    python main.py --dry-run    # Modo simulación (por defecto)
    python main.py --live       # Modo real (¡con dinero!)
    python main.py --scan-only  # Solo escanear, no apostar
"""

import os
import re
import sys
import json
import time
import asyncio
import logging
import argparse
from datetime import datetime
from pathlib import Path

# Agregar el directorio raíz al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import SAFETY, STATE
from core.market_scanner import MarketScanner
from core.ai_analyzer import AIAnalyzer
from core.risk_manager import RiskManager
from core.executor import TradeExecutor
from core.tracker import WinRateTracker
from modules.crypto_grinder import CryptoGrinder
from modules.crypto_daily import CryptoDailyStrategy
from modules.auto_redeem import AutoRedeemer
from modules.no_harvester import NOHarvester
from modules.weather_trader import WeatherTrader
from modules.stock_trader import StockTrader
from modules.telegram_monitor import TelegramMonitor

# ============================================================
# HELPER: Tiempo hasta resolución
# ============================================================

def _get_resolve_time(end_date_str: str) -> str:
    """Calcula cuánto falta para que un mercado resuelva. Retorna string legible."""
    if not end_date_str:
        return ""
    try:
        from datetime import timezone
        if end_date_str.endswith("Z"):
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        elif "+" in end_date_str[-6:]:
            end_dt = datetime.fromisoformat(end_date_str)
        else:
            end_dt = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
        diff = (end_dt - datetime.now(timezone.utc)).total_seconds()
        if diff <= 0:
            return "ya resuelto"
        if diff < 3600:
            return f"{diff/60:.0f} min"
        if diff < 86400:
            return f"{diff/3600:.1f} hrs"
        return f"{diff/86400:.0f} días"
    except:
        return ""


# ============================================================
# HELPER: Extraer nombres de equipos de una pregunta de mercado
# ============================================================

def _extract_teams(question: str) -> set:
    """
    Extrae nombres de equipos de la pregunta de un mercado.
    Se usa para prevenir apuestas correlacionadas en el mismo partido
    (ej. bet en "Real Madrid vs Girona O/U" + "Spread: Real Madrid").

    Maneja patrones comunes de Polymarket:
    - "Team A vs. Team B"
    - "Team A vs. Team B: O/U X"
    - "Spread: Team A (-X.X)"
    - "Will Team A win on ..."
    - "Game Handicap: Team A (-X.X) vs Team B (+X.X)"
    - "LoL: Team A vs Team B (BO3) - LCK ..."

    Retorna un set de nombres normalizados (lowercase, trimmed).
    Set vacío si no se puede extraer.
    """
    teams = set()
    if not question:
        return teams
    q = question.strip()

    def _clean(name: str) -> str:
        """Limpia un nombre de equipo: remueve paréntesis, dashes y BO#."""
        if not name:
            return ""
        # Remover todo después del primer "(" o " -" (handicap, BO3, etc.)
        name = re.sub(r'\s*[\(].*$', '', name)
        name = re.sub(r'\s*-\s*(?:Game|Map|BO|LCK|LEC|LCS|VCT|Rounds|Group|Regular).*$',
                      '', name, flags=re.IGNORECASE)
        return name.strip().lower()

    # Patrón 3 (específico, antes del genérico): "Handicap: X (-N) vs Y (+N)"
    m = re.search(
        r'handicap:\s*(.+?)\s*[\(\-\+].*?vs\.?\s+(.+?)\s*[\(\-\+]',
        q, re.IGNORECASE
    )
    if m:
        t1, t2 = _clean(m.group(1)), _clean(m.group(2))
        if t1:
            teams.add(t1)
        if t2:
            teams.add(t2)
        return teams

    # Patrón 1: "X vs. Y" o "X vs Y"
    m = re.search(r'(?:^|:\s*)([A-Z][^:]+?)\s+vs\.?\s+([A-Z][^:]+?)(?::|$)', q)
    if m:
        t1, t2 = _clean(m.group(1)), _clean(m.group(2))
        if t1:
            teams.add(t1)
        if t2:
            teams.add(t2)
        return teams

    # Patrón 2: "Spread: X (-N)" o "Spread: X (+N)"
    m = re.search(r'spread:\s*(.+?)\s*[\(\-\+]', q, re.IGNORECASE)
    if m:
        t = _clean(m.group(1))
        if t:
            teams.add(t)
        return teams

    # Patrón 4: "Will X win on..."
    m = re.search(r'will\s+(.+?)\s+win\s+on', q, re.IGNORECASE)
    if m:
        t = _clean(m.group(1))
        if t:
            teams.add(t)
        return teams

    return teams


# ============================================================
# SYNC DE POSICIONES ACTIVAS
# ============================================================

async def sync_positions():
    """
    Sincroniza posiciones reales de Polymarket con bets_placed.json.
    Esto evita duplicar apuestas al reiniciar el bot.
    """
    import aiohttp
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    if not pk:
        return

    from web3 import Web3
    address = Web3().eth.account.from_key(pk).address
    addresses = [addr for addr in [funder, address] if addr]

    positions = []
    logger = logging.getLogger("polybot.main")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for addr in addresses:
            for endpoint in [
                f"https://data-api.polymarket.com/positions?user={addr.lower()}",
                f"https://data-api.polymarket.com/positions?user={addr}",
            ]:
                try:
                    async with session.get(endpoint) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data and isinstance(data, list) and len(data) > 0:
                                positions = data
                                break
                except:
                    continue
            if positions:
                break

    if not positions:
        logger.info("   📂 No se encontraron posiciones activas")
        return

    # Actualizar bets_placed.json
    os.makedirs("data", exist_ok=True)
    try:
        with open("data/bets_placed.json", "r") as f:
            bets_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        bets_data = {"market_ids": [], "history": []}

    active_count = 0
    resolved_count = 0
    total_value = 0

    logger.info(f"\n   📊 POSICIONES ACTIVAS ({len(positions)}):")
    logger.info(f"   {'─' * 55}")

    for pos in positions:
        title = (pos.get("title") or pos.get("question") or
                 pos.get("eventTitle") or pos.get("market") or "?")
        market_id = str(pos.get("market_id") or pos.get("marketId") or
                       pos.get("conditionId") or "")
        asset = str(pos.get("asset") or pos.get("assetId") or "")
        side = pos.get("outcome") or pos.get("side") or "?"
        size = float(pos.get("size") or pos.get("shares") or 0)
        cur_price = float(pos.get("curPrice") or pos.get("price") or 0)
        value = float(pos.get("currentValue") or 0)
        if value == 0 and size > 0 and cur_price > 0:
            value = size * cur_price
        pnl = float(pos.get("cashPnl") or 0)
        pnl_pct = float(pos.get("percentPnl") or 0) * 100

        # End date para mostrar resolución
        end_date = pos.get("endDate") or pos.get("expirationDate") or ""

        total_value += value

        if value <= 0.01:
            resolved_count += 1
            status = "❌"
        else:
            active_count += 1
            status = "✅" if pnl >= 0 else "📉"

        # Agregar a bets_placed.json si no existe
        if asset and asset not in bets_data.get("market_ids", []):
            pass
        if market_id and market_id not in bets_data.get("market_ids", []):
            bets_data["market_ids"].append(market_id)
            bets_data["history"].append({
                "market_id": market_id,
                "question": str(title)[:80],
                "timestamp": datetime.now().isoformat(),
                "source": "sync"
            })

        # Log con emoji y resolución
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        resolve_str = ""
        if end_date:
            try:
                from datetime import timezone
                if end_date.endswith("Z"):
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                else:
                    end_dt = datetime.fromisoformat(end_date)
                now = datetime.now(timezone.utc)
                diff = (end_dt - now).total_seconds()
                if diff > 0:
                    if diff < 3600:
                        resolve_str = f" | ⏰ {diff/60:.0f}min"
                    elif diff < 86400:
                        resolve_str = f" | ⏰ {diff/3600:.1f}hrs"
                    else:
                        resolve_str = f" | ⏰ {diff/86400:.0f}días"
                else:
                    resolve_str = " | ⏰ RESUELTO"
            except:
                pass

        if value > 0.01:
            logger.info(
                f"   {status} {str(title)[:45]} | {side} "
                f"${value:.2f} ({pnl_str}){resolve_str}"
            )

    # Guardar bets_placed.json actualizado
    with open("data/bets_placed.json", "w") as f:
        json.dump(bets_data, f, indent=2)

    logger.info(f"   {'─' * 55}")
    logger.info(
        f"   💰 Total en posiciones: ${total_value:.2f} | "
        f"Activas: {active_count} | Resueltas: {resolved_count}"
    )
    logger.info(
        f"   📂 {len(bets_data['market_ids'])} mercados en anti-duplicado"
    )

# ============================================================
# LOGGING
# ============================================================

def setup_logging():
    """Configura el logging con archivo y consola."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f"polybot_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )

logger = logging.getLogger("polybot.main")


# ============================================================
# NOTIFICACIONES (Telegram opcional)
# ============================================================

async def send_telegram_notification(message: str):
    """Envía notificación a Telegram (si está configurado)."""
    from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        import aiohttp
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown"
            })
    except Exception as e:
        logger.error(f"Error enviando Telegram: {e}")


# ============================================================
# CICLO PRINCIPAL DEL BOT
# ============================================================

async def run_cycle(scanner: MarketScanner, analyzer: AIAnalyzer,
                    risk: RiskManager, executor: TradeExecutor,
                    redeemer: AutoRedeemer,
                    harvester: NOHarvester,
                    tracker: WinRateTracker,
                    weather_trader: WeatherTrader,
                    stock_trader: StockTrader,
                    grinder: CryptoGrinder = None,
                    crypto_daily: CryptoDailyStrategy = None,
                    telegram: TelegramMonitor = None,
                    scan_only: bool = False):
    """Ejecuta un ciclo completo con TODAS las estrategias."""

    cycle_start = datetime.now()

    # Modo fin de semana (17-Abr): sin STOCKS sáb/dom → SPORTS flojo = pérdida.
    # Capturamos defaults en la primera llamada y restauramos al final del
    # ciclo (idempotente) para que overrides no se filtren entre ciclos.
    if not hasattr(run_cycle, '_safety_defaults'):
        run_cycle._safety_defaults = {
            'max_bets_per_cycle': SAFETY.max_bets_per_cycle,
            'min_edge_required': SAFETY.min_edge_required,
        }
    _weekend = datetime.now().weekday() >= 5  # sáb=5, dom=6
    if _weekend:
        logger.info("   🏖️ MODO FIN DE SEMANA: límites más estrictos (max_bets=2, min_edge=8%)")
        SAFETY.max_bets_per_cycle = 2
        SAFETY.min_edge_required = 0.08

    # === AUTO-SCALING: Ajustar apuestas según capital ===
    # Sube con el capital, pero NUNCA baja de $4 (piso para recuperarse)
    bankroll = STATE.current_bankroll
    settings_max = SAFETY.max_bet_absolute  # $6 configurado por usuario
    if bankroll < 50:
        auto_max = 4.0  # Piso mínimo — permite recuperarse
    elif bankroll < 100:
        auto_max = 5.0
    elif bankroll < 200:
        auto_max = 8.0
    elif bankroll < 500:
        auto_max = 15.0
    elif bankroll < 1000:
        auto_max = 30.0
    elif bankroll < 5000:
        auto_max = 80.0
    elif bankroll < 20000:
        auto_max = 200.0
    else:
        auto_max = 500.0
    SAFETY.max_bet_absolute = min(auto_max, max(settings_max, bankroll * 0.05))

    # Auto-scale daily spend limit (50% del bankroll o mínimo $80)
    SAFETY.max_daily_spend = max(80.0, bankroll * 0.50)

    logger.info("=" * 60)
    logger.info(f"🔄 NUEVO CICLO - {cycle_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"   Bankroll: ${STATE.current_bankroll:.2f} | "
                f"P&L diario: ${STATE.daily_pnl:+.2f} | "
                f"Posiciones: {STATE.open_positions}/{SAFETY.max_open_positions}")
    logger.info(f"   Modo: {'🏃 DRY RUN (simulación)' if SAFETY.dry_run else '💰 LIVE (dinero real)'}")
    logger.info(f"   Max apuesta: ${SAFETY.max_bet_absolute:.0f} (auto-scale)")
    logger.info(f"   {tracker.get_summary()}")
    logger.info("=" * 60)

    # --- Paso 0: Kill switch y protección de capital ---
    # Calcular valor total (balance libre + posiciones) para kill switch
    _total_value = STATE.current_bankroll
    if not SAFETY.dry_run:
        try:
            import aiohttp as _aio_ks
            _pk_ks = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if _pk_ks:
                from web3 import Web3 as _W3ks
                _addr_ks = _W3ks().eth.account.from_key(_pk_ks).address
                _funder_ks = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
                for _a_ks in [_funder_ks, _addr_ks]:
                    if not _a_ks:
                        continue
                    try:
                        async with _aio_ks.ClientSession(timeout=_aio_ks.ClientTimeout(total=10)) as _s_ks:
                            async with _s_ks.get(f"https://data-api.polymarket.com/positions?user={_a_ks.lower()}") as _r_ks:
                                if _r_ks.status == 200:
                                    _pos_ks = await _r_ks.json()
                                    if _pos_ks and isinstance(_pos_ks, list):
                                        _pos_val = sum(float(p.get("currentValue") or 0) for p in _pos_ks if float(p.get("currentValue") or 0) > 0.01)
                                        _total_value = STATE.current_bankroll + _pos_val
                                        break
                    except:
                        continue
        except:
            pass

    # Actualizar ATH usando valor total (libre + posiciones)
    if _total_value > STATE.all_time_high:
        STATE.all_time_high = _total_value

    # Kill switch: si el TOTAL (no solo libre) cae 40% del ATH
    if STATE.all_time_high > 0 and _total_value < STATE.all_time_high * (1 - SAFETY.max_total_loss_pct):
        STATE.is_paused = True
        STATE.pause_reason = f"KILL SWITCH: Total ${_total_value:.2f} (libre ${STATE.current_bankroll:.2f} + posiciones) cayó más de {SAFETY.max_total_loss_pct:.0%} del ATH ${STATE.all_time_high:.2f}"
        logger.warning(f"🚨 {STATE.pause_reason}")
        if telegram:
            await telegram.send_error_alert(STATE.pause_reason)
    else:
        # Si no hay kill switch, asegurar que no estamos pausados por uno anterior
        if STATE.is_paused and "KILL SWITCH" in STATE.pause_reason:
            STATE.is_paused = False
            STATE.pause_reason = ""

    # Pausa por pérdidas consecutivas
    import time as _time_mod
    if STATE.consecutive_loss_pause_until > 0 and _time_mod.time() < STATE.consecutive_loss_pause_until:
        remaining = (STATE.consecutive_loss_pause_until - _time_mod.time()) / 60
        logger.info(f"⏸️ Pausa por {STATE.consecutive_losses} pérdidas seguidas. {remaining:.0f} min restantes.")
        return
    elif STATE.consecutive_loss_pause_until > 0 and _time_mod.time() >= STATE.consecutive_loss_pause_until:
        STATE.consecutive_loss_pause_until = 0
        STATE.consecutive_losses = 0
        logger.info("✅ Pausa por pérdidas terminada, reanudando operaciones")

    # --- Sincronizar open_positions desde posiciones reales ---
    if not SAFETY.dry_run:
        try:
            import aiohttp as _aio
            _funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
            _pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if _pk:
                from web3 import Web3 as _W3
                _addr = _W3().eth.account.from_key(_pk).address
                _addrs = [a for a in [_funder, _addr] if a]
                for _a in _addrs:
                    try:
                        async with _aio.ClientSession(timeout=_aio.ClientTimeout(total=10)) as _s:
                            async with _s.get(f"https://data-api.polymarket.com/positions?user={_a.lower()}") as _r:
                                if _r.status == 200:
                                    _pos = await _r.json()
                                    if _pos and isinstance(_pos, list):
                                        _active = sum(1 for p in _pos if float(p.get("currentValue") or 0) > 0.01)
                                        STATE.open_positions = _active
                                        break
                    except:
                        continue
        except:
            pass

    # --- Paso 1: Verificar si el bot está pausado ---
    if STATE.is_paused:
        logger.warning(f"⏸️ Bot PAUSADO: {STATE.pause_reason}")
        if "KILL SWITCH" in STATE.pause_reason:
            # Kill switch requiere reinicio manual — NO reanudar automáticamente
            logger.warning(f"   🚨 KILL SWITCH activo. Reinicia el bot manualmente para continuar.")
            return
        elif risk.check_cooldown_expired():
            STATE.is_paused = False
            STATE.pause_reason = ""
            logger.info("✅ Cooldown expirado, bot reanudado")
        else:
            logger.info(f"   Espera {SAFETY.cooldown_hours_after_stoploss}h antes de reanudar")
            return
            return

    # --- Escanear mercados (necesario para otras estrategias) ---
    logger.info("📊 Escaneando mercados...")
    markets = await scanner.scan_all_markets()

    if not markets:
        logger.warning("No se encontraron mercados que cumplan los filtros")
    else:
        logger.info(f"   Encontrados {len(markets)} mercados elegibles")

    if scan_only and markets:
        logger.info("\n📋 TOP 10 MERCADOS MÁS PRÓXIMOS A RESOLVERSE:")
        for i, m in enumerate(markets[:10], 1):
            logger.info(
                f"   {i}. [{m.days_until_resolution}d] {m.question[:50]}\n"
                f"      YES: ${m.outcome_yes_price:.3f} | "
                f"Liquidez: ${m.liquidity:,.0f} | "
                f"Vol: ${m.volume:,.0f}"
            )
        return

    # Reset daily spend tracker si es nuevo día
    today = datetime.now().strftime("%Y-%m-%d")
    if STATE.daily_spend_date != today:
        STATE.daily_spend = 0.0
        STATE.daily_spend_date = today
    STATE.cycle_bets = 0

    # Tracking de equipos apostados HOY para prevenir apuestas correlacionadas
    # (ej. O/U + Spread del mismo partido = riesgo duplicado).
    # Persiste entre ciclos del mismo día usando un atributo de la función.
    if not hasattr(run_cycle, '_daily_teams') or run_cycle._daily_teams.get("date") != today:
        run_cycle._daily_teams = {"date": today, "teams": set()}
    teams_bet_today = run_cycle._daily_teams["teams"]

    # ===== ESTRATEGIA 1: IA Deportes + Esports =====
    # Stocks: 100% WR (+$19.82) — mejor estrategia
    # Esports: 100% WR (+$16.80) — segunda mejor
    # Ahora incluimos TODOS los deportes + esports
    logger.info("\n" + "=" * 50)
    logger.info("🏆 ESTRATEGIA 1: IA Deportes + Esports")
    logger.info("=" * 50)
    if markets:
        try:
            # Filtrar mercados de deportes Y esports
            sports_kw = [
                # Esports
                "lol:", "league of legends", "counter-strike", "cs2", "cs:",
                "valorant", "dota", "esport", "BO3", "bo3", "BO5", "bo5",
                "game 1", "game 2", "game 3", "LEC", "LCS", "LCK", "VCT",
                "BLAST", "ESL", "IEM", "Major", "rainbow six", "r6s",
                "fortnite", "overwatch", "starcraft", "rocket league",
                # Fútbol europeo
                "FC", "vs.", "win on", "premier league", "la liga", "serie a",
                "bundesliga", "ligue 1", "champions league", "europa league",
                "copa", "libertadores", "sudamericana", "world cup",
                "euro", "eurocopa", "nations league", "fa cup", "copa del rey",
                # Fútbol europeo equipos
                "barcelona", "real madrid", "manchester", "liverpool", "arsenal",
                "chelsea", "juventus", "bayern", "psg", "inter", "milan",
                "atletico", "dortmund", "sporting", "benfica", "porto",
                "tottenham", "west ham", "newcastle", "everton", "leeds",
                "villarreal", "sevilla", "valencia", "real sociedad", "napoli",
                "roma", "lazio", "atalanta", "fiorentina", "ajax", "psv",
                # Fútbol latinoamericano — Liga MX
                "liga mx", "cruz azul", "america", "chivas", "tigres",
                "monterrey", "pumas", "león", "leon", "toluca", "pachuca",
                "santos laguna", "mazatlán", "querétaro", "necaxa", "fc juárez",
                # Fútbol latinoamericano — Brasil
                "brasileirão", "brasileirao", "serie a brasil",
                "flamengo", "palmeiras", "corinthians", "são paulo", "sao paulo",
                "santos", "fluminense", "vasco", "grêmio", "gremio",
                "internacional", "atlético mineiro", "botafogo", "bahia",
                # Fútbol latinoamericano — Argentina
                "liga argentina", "boca", "river plate", "racing club",
                "independiente", "san lorenzo", "estudiantes", "vélez",
                "velez", "lanús", "lanus", "newell", "rosario central",
                # Fútbol latinoamericano — Colombia
                "millonarios", "nacional", "junior", "tolima", "cali",
                "santa fe", "medellín", "medellin", "pereira", "once caldas",
                "bucaramanga", "pasto", "envigado",
                # Fútbol latinoamericano — otros
                "universitario", "alianza lima", "sporting cristal", "colo colo",
                "peñarol", "nacional uruguay", "olimpia", "cerro porteño",
                "lcdf", "liga pro", "barcelona sc", "emelec",
                # MLS
                "mls", "inter miami", "la galaxy", "lafc", "atlanta united",
                "seattle sounders", "nycfc", "new york red bulls",
                "columbus crew", "austin fc", "st. louis city",
                "portland timbers", "orlando city", "dc united",
                # NBA / Basketball
                "nba", "celtics", "lakers", "warriors", "nuggets", "76ers",
                "bucks", "heat", "knicks", "nets", "pacers", "cavaliers",
                "thunder", "suns", "rockets", "hornets", "pistons", "magic",
                "hawks", "grizzlies", "bulls", "clippers", "spurs", "kings",
                "blazers", "jazz", "pelicans", "raptors", "wizards", "timberwolves",
                "mavericks", "wolves",
                # NCAA Basketball / College
                "ncaab", "ncaa", "college basketball", "duke", "kentucky",
                "kansas", "uconn", "north carolina", "gonzaga", "michigan state",
                "march madness", "final four",
                # EuroLeague basketball
                "euroleague", "real madrid basket", "panathinaikos", "olympiacos",
                "cska moscow", "fenerbahçe", "fenerbahce", "baskonia", "asvel",
                "virtus bologna", "partizan", "crvena zvezda", "crvena zvesda",
                # NFL / Football americano
                "nfl", "chiefs", "eagles", "49ers", "cowboys", "ravens",
                "bills", "bengals", "dolphins", "lions", "packers",
                "super bowl", "touchdown", "quarterback",
                # NCAA Football
                "cfp", "college football", "ohio state", "alabama", "georgia",
                "michigan", "notre dame", "texas", "oregon",
                # MLB / Baseball
                "mlb", "yankees", "dodgers", "astros", "braves", "mets",
                "phillies", "padres", "cubs", "red sox", "giants",
                "world series", "home run", "rangers", "mariners", "blue jays",
                "rays", "orioles", "white sox", "guardians", "twins", "royals",
                "tigers", "angels", "athletics", "pirates", "marlins",
                "nationals", "brewers", "reds", "cardinals", "diamondbacks",
                "rockies",
                # NHL / Hockey
                "nhl", "stanley cup", "maple leafs", "bruins", "oilers",
                "panthers", "rangers", "avalanche", "hurricanes", "stars",
                "devils", "flyers", "lightning", "kraken", "golden knights",
                "jets", "flames", "canucks", "sharks", "senators", "blackhawks",
                "blues", "predators", "capitals", "penguins", "islanders",
                "wild", "kings", "sabres", "red wings", "coyotes", "ducks",
                # MMA / Boxing
                "ufc", "mma", "fight", "knockout", "round", "bellator", "pfl",
                "boxing", "heavyweight", "middleweight", "welterweight",
                "lightweight", "featherweight", "bantamweight",
                # Tennis
                "atp", "wta", "grand slam", "wimbledon", "us open",
                "french open", "australian open", "roland garros",
                "masters 1000", "djokovic", "alcaraz", "sinner", "medvedev",
                "zverev", "swiatek", "sabalenka", "rybakina",
                # Cricket
                "cricket", "ipl", "indian premier league", "bbl", "big bash",
                "t20", "odi", "test match", "icc", "champions trophy",
                "mumbai indians", "chennai super kings", "royal challengers",
                "kolkata knight riders", "delhi capitals", "rajasthan royals",
                # Rugby
                "rugby", "six nations", "rugby world cup", "super rugby",
                "all blacks", "springboks", "wallabies",
                # Golf
                "pga", "masters tournament", "us open golf", "the open",
                "pga championship", "ryder cup", "president cup",
                # Fórmula 1
                "formula 1", "f1", "grand prix", "verstappen", "hamilton",
                "leclerc", "norris", "piastri", "russell", "sainz",
                # Chess (ajedrez)
                "chess", "magnus carlsen", "hikaru nakamura", "fide",
                "world chess championship", "candidates",
                # Boxeo específico
                "canelo", "usyk", "tyson fury", "crawford", "inoue",
                # Pickleball
                "pickleball", "pro tour",
                # Table Tennis
                "table tennis", "ittf",
                # General deportes
                "spread:", "o/u", "over/under", "handicap", "total points",
                "game total", "moneyline", "map handicap", "game handicap",
                "race winner", "pole position", "podium",
                # Política/Geopolítica de corto plazo (20-Abr: Iran dio +$24).
                # El filtro de resolución <2 días del scanner bloquea
                # naturalmente cualquier mercado político de largo plazo.
                "diplomatic", "ceasefire", "sanctions", "summit",
                "meeting by", "talks", "negotiations", "deal",
                "iran", "tariff", "trade war", "embargo",
            ]

            # Excluir mercados que NO son deportes
            # "election"/"congress" removidos (20-Abr) para permitir política
            # de corto plazo (Iran diplomatic dio +$24). "president" queda
            # bloqueado porque mercados presidenciales son a largo plazo;
            # el filtro de resolución <2 días del scanner bloquea de todos
            # modos cualquier mercado político de largo plazo.
            exclude_kw = ["temperature", "weather", "temp", "°f", "°c",
                          "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                          "price of", "ipo", "valuation", "gdp", "inflation",
                          "president", "tweet", "musk",
                          # Bloquear mercados de empate (IA los miscalibra,
                          # ligas sudamericanas tienen ~25% draw rate)
                          "end in a draw", "draw?"]

            # Bloqueo de derivados esports (agregado 14-Apr): los 3 trades
            # en banda 0.40-0.50 que perdieron -$23.45 fueron todos Games
            # Total O/U y Map Handicap en esports (alta varianza, IA mal
            # calibrada). Moneyline de esports sigue pasando.
            esports_kw = [
                "lol:", "league of legends", "counter-strike", "cs2", "cs:",
                "valorant", "dota", "starcraft", "rocket league", "fortnite",
                "overwatch", "rainbow six", "r6s",
            ]
            # Derivados (Games Total, Map Handicap, Game Handicap) suelen
            # llegar con question = "Games Total: O/U 2.5" SIN prefijo del
            # juego, así que el filtro previo (is_esports AND is_derivative)
            # los dejaba pasar. Ahora bloqueamos por keyword de derivado
            # sin exigir match de esports_kw. Si en el futuro queremos
            # permitir handicap en fútbol etc., se agrega excepción manual.
            derivative_kw = [
                "games total", "map handicap", "game handicap",
            ]

            sports_markets = []
            for m in markets:
                q = (m.question or "").lower()
                # Tiene keyword de deportes?
                has_sport = any(kw.lower() in q for kw in sports_kw)
                # NO tiene keywords de exclusión?
                has_exclude = any(kw in q for kw in exclude_kw)
                # Bloquear SIEMPRE derivados (alta varianza, IA mal calibrada)
                is_derivative = any(kw in q for kw in derivative_kw)
                if is_derivative:
                    continue
                if has_sport and not has_exclude:
                    sports_markets.append(m)

            esports_markets = sports_markets  # Mantener nombre para compatibilidad

            if esports_markets:
                logger.info(f"   🏆 {len(esports_markets)} mercados de deportes/esports encontrados")
                analyses = await analyzer.analyze_markets_batch(esports_markets, max_to_analyze=8)

                for analysis in analyses:
                    # Override: si la IA dice SKIP pero el edge es > 10%
                    # y la probabilidad es >= 55%, forzar BET.
                    # Esto evita que la IA sea demasiado conservadora en
                    # oportunidades claras (ej. Sevilla 38% edge → SKIP absurdo).
                    ia_says_skip = (
                        hasattr(analysis, 'recommended_action') and
                        analysis.recommended_action.upper() == "SKIP"
                    )
                    if ia_says_skip:
                        if (analysis.edge > 0.10 and
                                analysis.estimated_probability >= 0.55):
                            logger.info(
                                f"   🔥 Override: {analysis.question[:35]} | "
                                f"Edge {analysis.edge:.1%} > 10% + Prob "
                                f"{analysis.estimated_probability:.0%} >= 55% → BET"
                            )
                        else:
                            logger.info(f"   ⏭️ {analysis.question[:40]}: SKIP")
                            continue
                    if hasattr(analysis, 'side') and analysis.side.upper() == "SKIP":
                        continue

                    # Bloquear underdogs: si el mercado paga el lado que
                    # compramos a menos de 40¢, es un underdog riesgoso.
                    # Los underdogs pierden con más frecuencia que su
                    # probabilidad implícita en mercados ilíquidos.
                    if analysis.market_price < 0.40:
                        logger.info(
                            f"   ❌ {analysis.question[:40]}: Underdog "
                            f"(mercado {analysis.market_price:.1%} < 40%)"
                        )
                        continue

                    # Prevenir apuestas correlacionadas en el mismo partido
                    # (ej. hoy apostamos O/U + Spread de Real Madrid, ambas
                    # perdieron = riesgo correlacionado).
                    analysis_teams = _extract_teams(analysis.question)
                    if analysis_teams and analysis_teams & teams_bet_today:
                        conflict = analysis_teams & teams_bet_today
                        logger.info(
                            f"   ⏭️ Skip: ya apostamos en partido de "
                            f"{list(conflict)[0]}"
                        )
                        continue

                    # Timing inteligente: saltar si el mercado resuelve muy
                    # lejos en el tiempo (>40h) o muy pronto (<30 min).
                    # Lejos: info puede cambiar. Pronto: poco margen de acción.
                    # Subido de 24h a 40h: se alinea con max_resolution_days=2
                    # del scanner y permite analizar partidos de mañana noche.
                    mkt_preview = {m.market_id: m for m in esports_markets}.get(analysis.market_id)
                    if mkt_preview and hasattr(mkt_preview, 'end_date') and mkt_preview.end_date:
                        try:
                            from datetime import timezone as _tz
                            _ed = mkt_preview.end_date
                            if _ed.endswith("Z"):
                                _edt = datetime.fromisoformat(_ed.replace("Z", "+00:00"))
                            elif "+" in _ed[-6:]:
                                _edt = datetime.fromisoformat(_ed)
                            else:
                                _edt = datetime.fromisoformat(_ed).replace(tzinfo=_tz.utc)
                            _hours = (_edt - datetime.now(_tz.utc)).total_seconds() / 3600
                            if _hours > 40:
                                logger.info(
                                    f"   ⏱️ {analysis.question[:35]}: resuelve en "
                                    f"{_hours:.0f}h (>40h, esperar)"
                                )
                                continue
                            if 0 < _hours < 0.5:
                                logger.info(
                                    f"   ⏱️ {analysis.question[:35]}: resuelve en "
                                    f"{_hours*60:.0f}min (<30min, muy tarde)"
                                )
                                continue
                        except Exception:
                            pass

                    if STATE.cycle_bets >= SAFETY.max_bets_per_cycle:
                        break
                    if STATE.daily_spend >= SAFETY.max_daily_spend:
                        break

                    # Filtros estrictos nuevos (17-Abr): SPORTS modo conservador
                    if analysis.market_price < 0.50 or analysis.market_price > 0.80:
                        logger.info(f"   ⏭️ SPORTS market_price {analysis.market_price:.1%} fuera rango 50-80%")
                        continue
                    if analysis.edge < 0.06:
                        logger.info(f"   ⏭️ SPORTS edge {analysis.edge:.1%} < 6%")
                        continue
                    if analysis.estimated_probability < 0.60:
                        logger.info(f"   ⏭️ SPORTS prob {analysis.estimated_probability:.1%} < 60%")
                        continue

                    mkt = {m.market_id: m for m in esports_markets}.get(analysis.market_id)
                    should_bet, reason, amount = risk.should_bet(
                        estimated_prob=analysis.estimated_probability,
                        market_price=analysis.market_price,
                        market_liquidity=mkt.liquidity if mkt else 50000,
                        market_volume=mkt.volume if mkt else 50000,
                        category="esports",
                        strategy="SPORTS"
                    )

                    # Reducir bet size a 50% en mercados de alta varianza
                    # (spreads, handicaps, O/U). Estos son más coin-flip que
                    # moneyline y hoy perdimos en NAVI, MOUZ, OG (-$21 total).
                    _q_lower = (analysis.question or "").lower()
                    _is_risky = any(kw in _q_lower for kw in [
                        "spread:", "handicap", "o/u", "over/under",
                        "total:", "map handicap", "game handicap"
                    ])
                    if should_bet and _is_risky:
                        amount = max(round(amount * 0.50, 2), 1.00)
                        logger.info(
                            f"   📉 Bet reducido 50% por alta varianza "
                            f"(spread/handicap/O/U), piso $1 Polymarket: ${amount:.2f}"
                        )

                    if should_bet:
                        result = await executor.execute_bet(analysis, amount)
                        status = result.get('status', 'UNKNOWN')
                        if status in ("EXECUTED", "SIMULATED"):
                            tracker.add_trade(
                                market_id=str(analysis.market_id),
                                question=analysis.question,
                                side=analysis.side,
                                amount=amount,
                                price=analysis.market_price,
                                strategy="SPORTS",
                                edge=getattr(analysis, "edge", None),
                                prob=getattr(analysis, "estimated_probability", None),
                            )
                            STATE.cycle_bets += 1
                            STATE.daily_spend += amount
                            # Registrar equipos para evitar apuestas correlacionadas
                            teams_bet_today.update(analysis_teams)
                            if telegram and status == "EXECUTED":
                                _rt = _get_resolve_time(mkt.end_date if mkt else "")
                                await telegram.send_trade_alert(
                                    "IA", analysis.question, analysis.side,
                                    amount, analysis.market_price, analysis.edge, _rt)
                                telegram.log_trade("ESPORTS", analysis.question, analysis.side, amount)
                            logger.info(f"   ✅ Esports: {analysis.question[:40]} | ${amount:.2f} {analysis.side}")
                        elif telegram and status not in ("EXECUTED", "SIMULATED"):
                            # Notificar errores de ejecución por Telegram
                            _err_msg = result.get('error', status)
                            _err_clean = str(_err_msg)[:150]
                            # Detectar geoblock (error crítico)
                            if "restricted" in _err_clean.lower() or "403" in _err_clean:
                                await telegram.send_error_alert(
                                    f"GEOBLOCK: No se puede operar desde esta IP.\n"
                                    f"Mercado: {analysis.question[:40]}\n"
                                    f"Cambiar servidor a otra region."
                                )
                            else:
                                await telegram.send_error_alert(
                                    f"Orden falló: {_err_clean}\n"
                                    f"Mercado: {analysis.question[:40]}"
                                )
                    else:
                        logger.info(f"   ❌ {analysis.question[:35]}: {reason[:40]}")
            else:
                logger.info("   🏆 No hay mercados de deportes/esports activos ahora")
        except Exception as e:
            logger.error(f"   Error en IA Esports: {e}")

    # (Harvest, Weather, Crypto — eliminados del ciclo)

    # ===== ESTRATEGIA 5: Stock Market Trader =====
    logger.info("\n" + "=" * 50)
    logger.info("📈 ESTRATEGIA 5: Stock Market Trader (S&P/NASDAQ/Dow)")
    logger.info("=" * 50)
    # Guardas de límite global ANTES de invocar al stock trader.
    # Stock trader no revisa estos límites internamente, así que los
    # aplicamos aquí para prevenir gastar más allá del presupuesto diario
    # o del máximo de apuestas por ciclo.
    if STATE.cycle_bets >= SAFETY.max_bets_per_cycle:
        logger.info(f"   ⏭️ Max apuestas/ciclo alcanzado ({SAFETY.max_bets_per_cycle})")
    elif STATE.daily_spend >= SAFETY.max_daily_spend:
        logger.info(f"   ⏭️ Max gasto diario alcanzado (${SAFETY.max_daily_spend:.0f})")
    else:
        try:
            stock_trade = await stock_trader.run_cycle()
            if stock_trade:
                status = stock_trade.get("status", "UNKNOWN")
                if status == "EXECUTED":
                    logger.info(f"   ✅ Stock trade ejecutado: ${stock_trade['amount']:.2f} "
                               f"{stock_trade.get('side', '')} | Edge: {stock_trade.get('edge', 0):.1%}")
                    tracker.add_trade(
                        market_id=stock_trade.get("market_id", ""),
                        question=stock_trade.get("question", ""),
                        side=stock_trade.get("side", ""),
                        amount=stock_trade["amount"],
                        price=stock_trade.get("price", 0.50),
                        strategy="STOCKS",
                        edge=stock_trade.get("edge"),
                        prob=stock_trade.get("prob"),
                    )
                    # Contabilizar el stock trade en los contadores globales.
                    # stock_trader usa su propio py-clob-client (no pasa por
                    # executor.execute_bet), así que si no hacemos esto:
                    # - el summary dice "No se ejecutaron órdenes" (falso)
                    # - max_daily_spend no limita stocks (riesgo real)
                    # - max_bets_per_cycle no limita stocks
                    # - la alerta "2h sin apostar" dispara aunque sí apostamos
                    STATE.cycle_bets += 1
                    STATE.daily_spend += stock_trade["amount"]
                    executor.executed_orders.append({
                        "mode": "LIVE",
                        "side": stock_trade.get("side", ""),
                        "amount_usd": stock_trade["amount"],
                        "question": stock_trade.get("question", "")[:60],
                        "price": stock_trade.get("price", 0.50),
                        "edge": stock_trade.get("edge", 0),
                        "status": "EXECUTED",
                    })
                    if telegram:
                        await telegram.send_trade_alert(
                            "STOCKS", stock_trade.get("question", ""),
                            stock_trade.get("side", ""), stock_trade["amount"],
                            stock_trade.get("price", 0.50), stock_trade.get("edge", 0), "cierre de hoy")
                        telegram.log_trade("STOCKS", stock_trade.get("question", ""), stock_trade.get("side", ""), stock_trade["amount"])
                        logger.info(f"   ⏳ Resuelve en: cierre de hoy")
                elif status == "SIMULATED":
                    logger.info(f"   🏃 Stock simulado: ${stock_trade['amount']:.2f} {stock_trade.get('side', '')}")
                elif status == "FAILED":
                    logger.info(f"   ❌ Stock trade falló")
                    if telegram:
                        _err = stock_trade.get('error', 'FAILED')
                        await telegram.send_error_alert(
                            f"Stock trade falló: {str(_err)[:100]}\n"
                            f"Mercado: {stock_trade.get('question', '?')[:40]}"
                        )
                else:
                    logger.info(f"   ℹ️ Stocks: {status}")
        except Exception as e:
            logger.error(f"   Error en Stock Trader: {e}")
            if telegram:
                await telegram.send_error_alert(f"Error Stock Trader: {str(e)[:100]}")

    # ===== ESTRATEGIA 6: Crypto Daily (BTC/ETH/SOL/XRP) =====
    # DESACTIVADA 17-Abr: n=7, WR 43%, -$9.09 neto. Data suficiente para decidir.
    crypto_enabled = False
    if crypto_daily and not crypto_enabled:
        logger.info("   ⏭️ Crypto strat desactivada (3/7 WR, -$9)")
    if crypto_daily and crypto_enabled:
        logger.info("\n" + "=" * 50)
        logger.info("₿ ESTRATEGIA 6: Crypto Daily (BTC/ETH/SOL/XRP)")
        logger.info("=" * 50)
        try:
            signals = await crypto_daily.run_cycle()
            for signal in signals:
                if STATE.cycle_bets >= SAFETY.max_bets_per_cycle:
                    break
                if STATE.daily_spend >= SAFETY.max_daily_spend:
                    break

                # Bloquear si la prob es muy baja (< 55%)
                if signal["prob"] < 0.55:
                    logger.info(
                        f"   ⏭️ {signal['question'][:40]}: prob {signal['prob']:.0%} < 55%"
                    )
                    continue

                if signal["amount"] < SAFETY.min_bet_size:
                    logger.info(
                        f"   ⏭️ Monto muy bajo: ${signal['amount']:.2f}"
                    )
                    continue

                # Construir analysis object minimal para executor
                from core.ai_analyzer import MarketAnalysis
                analysis = MarketAnalysis(
                    market_id=signal["market_id"],
                    question=signal["question"],
                    estimated_probability=signal["prob"],
                    confidence=0.7,
                    market_price=signal["price"],
                    edge=signal["edge"],
                    reasoning=signal["reason"],
                    side=signal["side"],
                    recommended_action="BET",
                    risk_factors=[],
                    key_evidence=[signal["reason"]],
                )

                result = await executor.execute_bet(analysis, signal["amount"])
                status = result.get('status', 'UNKNOWN')
                if status in ("EXECUTED", "SIMULATED"):
                    tracker.add_trade(
                        market_id=signal["market_id"],
                        question=signal["question"],
                        side=signal["side"],
                        amount=signal["amount"],
                        price=signal["price"],
                        strategy="CRYPTO",
                        edge=signal.get("edge"),
                        prob=signal.get("prob"),
                    )
                    STATE.cycle_bets += 1
                    STATE.daily_spend += signal["amount"]
                    crypto_daily.last_bet_time[signal["crypto"]] = time.time()
                    crypto_daily._save_bet(signal["market_id"], signal["question"])
                    logger.info(
                        f"   ✅ Crypto: {signal['crypto']} "
                        f"${signal['amount']:.2f} {signal['side']}"
                    )
                    if telegram and status == "EXECUTED":
                        await telegram.send_trade_alert(
                            "CRYPTO", signal["question"], signal["side"],
                            signal["amount"], signal["price"],
                            signal["edge"], signal.get("end_date", "")
                        )
                        telegram.log_trade(
                            "CRYPTO", signal["question"],
                            signal["side"], signal["amount"]
                        )
                elif telegram and status not in ("EXECUTED", "SIMULATED"):
                    _err = result.get('error', status)
                    await telegram.send_error_alert(
                        f"Crypto orden falló: {str(_err)[:100]}"
                    )
        except Exception as e:
            logger.error(f"   Error en Crypto Daily: {e}")
            if telegram:
                await telegram.send_error_alert(f"Error Crypto Daily: {str(e)[:100]}")

    # ===== ACTUALIZAR P&L desde tracker =====
    if not SAFETY.dry_run:
        try:
            from web3 import Web3 as _W3p
            _pk_pnl = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if _pk_pnl:
                _addr_pnl = _W3p().eth.account.from_key(_pk_pnl).address
                await tracker.check_results(_addr_pnl)
                # Update P&L from tracker
                _won = [t for t in tracker.trades if t["result"] == "WON"]
                _lost = [t for t in tracker.trades if t["result"] == "LOST"]
                _total_profit = sum(t["profit"] for t in _won)
                _total_loss = sum(t["profit"] for t in _lost)
                STATE.total_pnl = _total_profit + _total_loss
                # Daily P&L - filter today's resolved trades
                _today = datetime.now().strftime("%Y-%m-%d")
                _today_trades = [t for t in _won + _lost
                                 if t.get("timestamp", "").startswith(_today)]
                STATE.daily_pnl = sum(t["profit"] for t in _today_trades)

                # Track pérdidas consecutivas
                _all_resolved = sorted(
                    [t for t in tracker.trades if t["result"] in ("WON", "LOST")],
                    key=lambda t: t.get("timestamp", ""),
                    reverse=True
                )
                # Contar pérdidas seguidas desde el trade más reciente
                _consec = 0
                for t in _all_resolved[:10]:  # Solo últimos 10
                    if t["result"] == "LOST":
                        _consec += 1
                    else:
                        break
                STATE.consecutive_losses = _consec

                # Si 5+ pérdidas seguidas, pausar 30 minutos
                if _consec >= SAFETY.max_consecutive_losses and STATE.consecutive_loss_pause_until == 0:
                    import time as _t
                    STATE.consecutive_loss_pause_until = _t.time() + (SAFETY.consecutive_loss_pause_minutes * 60)
                    logger.warning(f"⚠️ {_consec} pérdidas seguidas — pausa de {SAFETY.consecutive_loss_pause_minutes} min")
                    if telegram:
                        await telegram.send_error_alert(
                            f"{_consec} pérdidas seguidas. Bot pausado {SAFETY.consecutive_loss_pause_minutes} min."
                        )
        except Exception as _e:
            logger.debug(f"Error actualizando P&L: {_e}")

    # ===== AUTO-COBRO (cada 4 ciclos ≈ 1 hora) =====
    # Usa subprocess para ejecutar redeem.py directamente (el que SÍ funciona)
    if not SAFETY.dry_run and not hasattr(run_cycle, '_redeem_counter'):
        run_cycle._redeem_counter = 0
    if not SAFETY.dry_run:
        run_cycle._redeem_counter = getattr(run_cycle, '_redeem_counter', 0) + 1
        if run_cycle._redeem_counter >= 4:
            run_cycle._redeem_counter = 0
            logger.info("\n" + "=" * 50)
            logger.info("💰 AUTO-COBRO (cada ~1 hora)")
            logger.info("=" * 50)
            try:
                # Leer balance REAL antes del redeem
                _bal_before_redeem = STATE.current_bankroll
                try:
                    from web3 import Web3 as _W3br
                    _pk_br = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
                    _alchemy_br = os.getenv("ALCHEMY_RPC_URL", "")
                    _rpc_br = _alchemy_br or "https://polygon-bor-rpc.publicnode.com"
                    _w3br = _W3br(_W3br.HTTPProvider(_rpc_br, request_kwargs={'timeout': 10}))
                    if _w3br.is_connected():
                        _eoa_br = _w3br.eth.account.from_key(_pk_br).address
                        _usdc_abi_br = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]
                        _usdc_br = _w3br.eth.contract(address=_w3br.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"), abi=_usdc_abi_br)
                        _bal_before_redeem = _usdc_br.functions.balanceOf(_eoa_br).call() / 1e6
                except:
                    pass

                import subprocess
                result = subprocess.run(
                    [sys.executable, "redeem.py"],
                    capture_output=True, text=True, timeout=300,
                    encoding='utf-8', errors='replace'
                )
                output = result.stdout or ""

                # Leer balance REAL después del redeem
                import time as _time_redeem
                _time_redeem.sleep(3)
                _bal_after_redeem = _bal_before_redeem
                try:
                    if _w3br.is_connected():
                        _bal_after_redeem = _usdc_br.functions.balanceOf(_eoa_br).call() / 1e6
                except:
                    pass

                _real_diff = round(_bal_after_redeem - _bal_before_redeem, 2)

                # Log del resultado y extraer qué mercados se cobraron
                # Guardamos estructurado (title + amount) para:
                #   1) Telegram (mensaje del cobro)
                #   2) tracker.mark_redeemed_by_title() (actualizar win rate)
                _redeemed_markets_data = []
                for line in output.split("\n"):
                    line = line.strip()
                    if "Diferencia:" in line or "Cobradas:" in line:
                        logger.info(f"   {line}")
                    # Detectar mercados cobrados con ganancia
                    # Ejemplos:
                    #   "+$9.34 WIN | Games Total: O/U 2.5"
                    #   "+$3.92 VENDIDO en mercado | Real Madrid CF vs..."
                    if ("WIN" in line or "VENDIDO" in line) and "|" in line and "$" in line:
                        try:
                            # Extraer título después del "|"
                            parts = line.split("|", 1)
                            if len(parts) > 1:
                                _title = parts[1].strip()[:60]
                                # Extraer cantidad
                                import re as _re
                                _amt_match = _re.search(r'\+?\$(\d+\.?\d*)', parts[0])
                                _amt_val = float(_amt_match.group(1)) if _amt_match else 0
                                if _title and _amt_val > 0:
                                    _redeemed_markets_data.append({
                                        "title": _title,
                                        "amount": _amt_val,
                                    })
                        except Exception:
                            pass
                # Formato string para el alerta de Telegram (compatibilidad)
                _redeemed_markets = [
                    f"{m['title']} (+${m['amount']:.2f})"
                    for m in _redeemed_markets_data
                ]

                logger.info(f"   Balance real: ${_bal_before_redeem:.2f} → ${_bal_after_redeem:.2f}")

                if _real_diff > 0.10:
                    # Cobro REAL confirmado
                    STATE.current_bankroll = _bal_after_redeem
                    logger.info(f"   ✅ Cobro real: +${_real_diff:.2f}")

                    # Notificar al tracker que estos trades ganaron.
                    # Sin este paso el win rate quedaba desactualizado:
                    # el trade cobrado seguía como PENDING porque
                    # check_results() no podía detectarlo (posición ya
                    # desaparecida + Gamma API sin winningOutcome aún).
                    for _m in _redeemed_markets_data:
                        try:
                            tracker.mark_redeemed_by_title(
                                _m["title"], _m["amount"]
                            )
                        except Exception as _e:
                            logger.debug(f"   tracker.mark_redeemed error: {_e}")

                    if telegram:
                        _count = len(_redeemed_markets) or 1
                        await telegram.send_redeem_alert(
                            _real_diff, _count, _bal_after_redeem,
                            markets=_redeemed_markets
                        )
                elif _real_diff < -0.10:
                    # Balance bajó (posible venta de posición con pérdida)
                    STATE.current_bankroll = _bal_after_redeem
                    logger.info(f"   📉 Balance bajó: ${_real_diff:.2f}")
                else:
                    STATE.current_bankroll = _bal_after_redeem
                    logger.info(f"   Sin cambios reales en balance")

                if result.returncode != 0 and result.stderr:
                    logger.debug(f"   Redeem stderr: {result.stderr[:100]}")
            except subprocess.TimeoutExpired:
                logger.warning("   Auto-cobro timeout (>5 min)")
            except Exception as e:
                logger.error(f"   Error auto-cobro: {e}")

    # ===== RESUMEN COMPLETO =====
    logger.info("\n" + "=" * 60)
    logger.info("📋 RESUMEN DEL CICLO")
    logger.info("=" * 60)
    summary = executor.get_execution_summary()
    logger.info(summary)
    logger.info(f"💰 Bankroll: ${STATE.current_bankroll:.2f} | "
                f"Total trades: {STATE.total_trades}")
    logger.info("=" * 60)

    # ===== ALERTA: Bot sin apostar por mucho tiempo =====
    # Si pasan 8 ciclos (~2 horas) sin una sola apuesta, alertar UNA vez.
    if not hasattr(run_cycle, '_no_bet_counter'):
        run_cycle._no_bet_counter = 0
        run_cycle._no_bet_alerted = False

    if STATE.cycle_bets == 0:
        run_cycle._no_bet_counter += 1
    else:
        run_cycle._no_bet_counter = 0
        run_cycle._no_bet_alerted = False

    if run_cycle._no_bet_counter >= 8 and not run_cycle._no_bet_alerted:
        _hours_idle = run_cycle._no_bet_counter * SAFETY.scan_interval_minutes / 60
        logger.warning(f"⚠️ Bot lleva {_hours_idle:.1f}h sin apostar ({run_cycle._no_bet_counter} ciclos)")
        if telegram:
            await telegram.send(
                f"⚠️ SIN APUESTAS\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"El bot lleva {_hours_idle:.1f} horas sin encontrar oportunidades.\n"
                f"Ciclos revisados: {run_cycle._no_bet_counter}\n"
                f"Balance: ${STATE.current_bankroll:.2f}\n"
                f"Esto puede ser normal en horarios sin deportes,\n"
                f"o puede indicar un problema de filtros/IA.\n"
                f"Revisa los logs si persiste."
            )
        run_cycle._no_bet_alerted = True

    # Notificar por Telegram (reporte periódico)
    if telegram:
        try:
            # Obtener posiciones actuales para el reporte
            _positions_tg = []
            if not SAFETY.dry_run:
                try:
                    import aiohttp as _aio_tg
                    _pk_tg = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
                    if _pk_tg:
                        from web3 import Web3 as _W3tg
                        _addr_tg = _W3tg().eth.account.from_key(_pk_tg).address
                        _funder_tg = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
                        for _a_tg in [_funder_tg, _addr_tg]:
                            if not _a_tg:
                                continue
                            try:
                                async with _aio_tg.ClientSession(timeout=_aio_tg.ClientTimeout(total=10)) as _s_tg:
                                    async with _s_tg.get(f"https://data-api.polymarket.com/positions?user={_a_tg.lower()}") as _r_tg:
                                        if _r_tg.status == 200:
                                            _positions_tg = await _r_tg.json()
                                            if _positions_tg:
                                                break
                            except:
                                continue
                except:
                    pass

            # Reset diario: si _daily_stock_count es de un día anterior,
            # se considera 0 para el reporte de hoy.
            _today_str = datetime.now().strftime("%Y-%m-%d")
            _stock_info = getattr(stock_trader, "_daily_stock_count", {}) or {}
            _stock_count = int(_stock_info.get("count", 0)) \
                if _stock_info.get("date") == _today_str else 0

            await telegram.send_periodic_report(
                bankroll=STATE.current_bankroll,
                pnl_total=STATE.total_pnl,
                positions=_positions_tg or [],
                tracker_summary=tracker.get_summary(exclude_strategies=["CRYPTO"]),
                stock_daily_count=_stock_count,
                stock_daily_limit=2,
            )
        except Exception as _e_tg:
            logger.debug(f"Error reporte Telegram: {_e_tg}")

    # Restaurar SAFETY defaults (idempotente) — weekend overrides no deben
    # filtrarse a lunes si el ciclo terminó por excepción.
    SAFETY.max_bets_per_cycle = run_cycle._safety_defaults['max_bets_per_cycle']
    SAFETY.min_edge_required = run_cycle._safety_defaults['min_edge_required']


# ============================================================
# MAIN
# ============================================================

async def main():
    """Punto de entrada principal del bot."""
    parser = argparse.ArgumentParser(description="PolyBot - Bot de Polymarket")
    parser.add_argument("--live", action="store_true",
                        help="Activar modo LIVE (dinero real)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Modo simulación (por defecto)")
    parser.add_argument("--scan-only", action="store_true",
                        help="Solo escanear mercados, no apostar")
    parser.add_argument("--once", action="store_true",
                        help="Ejecutar un solo ciclo y salir")
    parser.add_argument("--stop-at", type=int, default=None,
                        help="Hora para auto-stop (ej: 18 = 6PM)")
    args = parser.parse_args()

    setup_logging()

    # Modo de operación
    if args.live:
        SAFETY.dry_run = False
        logger.warning("⚠️ " * 20)
        logger.warning("   MODO LIVE ACTIVADO - DINERO REAL")
        logger.warning("⚠️ " * 20)
    else:
        SAFETY.dry_run = True
        logger.info("🏃 Modo DRY RUN (simulación)")

    # Cargar variables de entorno
    from dotenv import load_dotenv
    load_dotenv()

    # Verificar configuración
    from config.settings import ANTHROPIC_API_KEY
    if not ANTHROPIC_API_KEY:
        logger.error("❌ ANTHROPIC_API_KEY no encontrada en .env")
        logger.error("   Crea un archivo .env con tu API key de Anthropic")
        return

    # Inicializar componentes
    scanner = MarketScanner()
    analyzer = AIAnalyzer()
    risk = RiskManager()
    executor = TradeExecutor(risk)

    logger.info("🤖 PolyBot MULTI-ESTRATEGIA iniciado")

    # En modo LIVE, obtener balance real de la wallet
    if not SAFETY.dry_run:
        try:
            from web3 import Web3
            rpcs = ["https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic",
                    "https://polygon-rpc.com", "https://rpc.ankr.com/polygon",
                    "https://polygon.llamarpc.com"]
            w3 = None
            for rpc in rpcs:
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
                    if w3.is_connected():
                        break
                except:
                    continue
            if w3 and w3.is_connected():
                USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
                abi = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]
                pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
                account = w3.eth.account.from_key(pk)
                usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E), abi=abi)
                balance = usdc.functions.balanceOf(account.address).call() / 1e6
                STATE.current_bankroll = balance
                # Resetear ATH al balance actual al iniciar (evita kill switch falso)
                STATE.all_time_high = balance
                STATE.is_paused = False  # Limpiar pausas previas al reiniciar
                STATE.pause_reason = ""
                logger.info(f"   💰 Balance real USDC.e: ${balance:.2f} (ATH reseteado)")
            else:
                logger.warning("   ⚠️ No se pudo conectar a Polygon para balance")
        except Exception as e:
            logger.warning(f"   ⚠️ Error obteniendo balance: {e}")

        # Sincronizar posiciones activas con Polymarket
        try:
            await sync_positions()
        except Exception as e:
            logger.warning(f"   ⚠️ Error sincronizando posiciones: {e}")

    logger.info(f"   Capital: ${STATE.current_bankroll:.2f}")
    logger.info(f"   Máx por apuesta: ${SAFETY.max_bet_absolute:.2f} ({SAFETY.max_bet_pct:.0%})")
    logger.info(f"   Edge mínimo: {SAFETY.min_edge_required:.0%}")
    logger.info(f"   Kelly fracción: {SAFETY.kelly_fraction}")
    logger.info(f"   Escaneo cada: {SAFETY.scan_interval_minutes} min")
    logger.info(f"   Estrategias activas:")
    logger.info(f"     1. 📈 STOCKS (S&P/NASDAQ/Russell/Dow)")
    logger.info(f"     2. 🏆 DEPORTES + ESPORTS (NBA, NHL, fútbol, MMA, LoL, CS)")

    # Inicializar componentes auxiliares
    redeemer = AutoRedeemer()
    harvester = NOHarvester()
    tracker = WinRateTracker()
    weather_trader = WeatherTrader()
    stock_trader = StockTrader()
    grinder = CryptoGrinder()
    crypto_daily = CryptoDailyStrategy()
    telegram = TelegramMonitor()

    # Enviar notificación de inicio
    if telegram.enabled:
        mode = "LIVE 💰" if not SAFETY.dry_run else "DRY RUN 🏃"
        await telegram.send_startup(STATE.current_bankroll, mode)

    try:
        if args.once or args.scan_only:
            # Un solo ciclo
            await run_cycle(scanner, analyzer, risk, executor,
                          redeemer, harvester, tracker,
                          weather_trader, stock_trader, grinder,
                          crypto_daily, telegram, args.scan_only)
        else:
            # Loop continuo
            last_ia_scan = 0
            ia_interval = SAFETY.scan_interval_minutes * 60

            stop_hour = getattr(args, 'stop_at', None)
            if stop_hour:
                logger.info(f"⏰ Auto-stop programado a las {stop_hour}:00")

            logger.info("\n🚀 Modo continuo: 2 estrategias activas\n")

            while True:
                now = time.time()

                if stop_hour and datetime.now().hour >= stop_hour:
                    logger.info(f"\n🛑 Auto-stop: Son las {datetime.now().strftime('%H:%M')}. Bot descansando.")
                    logger.info(f"   Balance final: ${STATE.current_bankroll:.2f}")
                    logger.info(f"   {tracker.get_summary()}")
                    if telegram.enabled:
                        await telegram.send_shutdown(
                            STATE.current_bankroll, STATE.total_trades,
                            tracker.get_summary())
                    # Generar reporte diario automático
                    try:
                        from daily_report import generate_report
                        report = await generate_report()
                        if telegram.enabled:
                            # Enviar resumen corto por Telegram
                            _lines = report.split("\n")
                            _summary = "\n".join(_lines[:35])  # Primeras 35 líneas
                            await telegram.send(f"📊 *REPORTE DIARIO*\n```\n{_summary}\n```")
                    except Exception as _e:
                        logger.error(f"Error generando reporte: {_e}")
                    break

                if now - last_ia_scan >= ia_interval:
                    logger.info("\n" + "=" * 50)
                    logger.info("🧠 Ciclo completo — Stocks + Deportes")
                    logger.info("=" * 50)
                    await run_cycle(scanner, analyzer, risk, executor,
                                  redeemer, harvester, tracker,
                                  weather_trader, stock_trader, grinder,
                                  crypto_daily, telegram)
                    last_ia_scan = now
                    logger.info(f"\n⏰ Próximo ciclo en {SAFETY.scan_interval_minutes} min")

                await asyncio.sleep(5)

    except KeyboardInterrupt:
        logger.info("\n👋 Bot detenido por el usuario")
    finally:
        # Enviar reporte final por Telegram
        if telegram.enabled:
            try:
                await telegram.send_shutdown(
                    STATE.current_bankroll, STATE.total_trades,
                    tracker.get_summary())
            except:
                pass

        await scanner.close()
        await analyzer.close()
        await redeemer.close()
        await weather_trader.close()
        await stock_trader.close()
        await grinder.close()
        await crypto_daily.close()
        await telegram.close()

        # Guardar log final
        final_summary = risk.get_daily_summary()
        summary_path = Path("data") / f"summary_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        summary_path.parent.mkdir(exist_ok=True)
        with open(summary_path, "w") as f:
            json.dump(final_summary, f, indent=2)
        logger.info(f"📁 Resumen guardado en {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
