"""
PolyBot - Strategy 5: Stock Market Trader
==========================================
Tradea mercados de S&P 500, NASDAQ, Dow Jones en Polymarket.
El trade S&P 500 del Día 1 fue el más exitoso (+$16).

Usa Yahoo Finance (sin key) para datos en tiempo real.
"""

import os
import re
import json
import time
import logging
import aiohttp
from typing import Optional, Dict, List
from datetime import datetime, timezone, timedelta
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.stocks")

GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Índices bursátiles + acciones individuales con mercados diarios en Polymarket
INDICES = {
    "sp500":   {"symbol": "^GSPC", "futures": "ES=F",
                "aliases": ["s&p", "s&p 500", "sp500", "s&p500", "spy", "spx"],
                "name": "S&P 500"},
    "nasdaq":  {"symbol": "^IXIC", "futures": "NQ=F",
                "aliases": ["nasdaq", "qqq", "ndx", "nasdaq-100", "nasdaq 100"],
                "name": "NASDAQ"},
    "dow":     {"symbol": "^DJI",  "futures": "YM=F",
                "aliases": ["dow", "dow jones", "djia", "dia"],
                "name": "Dow Jones"},
    "russell": {"symbol": "^RUT",  "futures": "RTY=F",
                "aliases": ["russell", "russell 2000", "iwm"],
                "name": "Russell 2000"},
    # Acciones individuales (mercados Up/Down diarios en Polymarket)
    "nvda":    {"symbol": "NVDA",  "futures": "NVDA",
                "aliases": ["nvidia", "nvda"],
                "name": "NVIDIA"},
    "googl":   {"symbol": "GOOGL", "futures": "GOOGL",
                "aliases": ["google", "googl", "alphabet"],
                "name": "Google"},
    "aapl":    {"symbol": "AAPL",  "futures": "AAPL",
                "aliases": ["apple", "aapl"],
                "name": "Apple"},
    "tsla":    {"symbol": "TSLA",  "futures": "TSLA",
                "aliases": ["tesla", "tsla"],
                "name": "Tesla"},
    "meta":    {"symbol": "META",  "futures": "META",
                "aliases": ["meta", "facebook"],
                "name": "Meta"},
    "amzn":    {"symbol": "AMZN",  "futures": "AMZN",
                "aliases": ["amazon", "amzn"],
                "name": "Amazon"},
    "msft":    {"symbol": "MSFT",  "futures": "MSFT",
                "aliases": ["microsoft", "msft"],
                "name": "Microsoft"},
    "nflx":    {"symbol": "NFLX",  "futures": "NFLX",
                "aliases": ["netflix", "nflx"],
                "name": "Netflix"},
    # Commodities (mercados de materias primas en Polymarket)
    "gold":    {"symbol": "GC=F",  "futures": "GC=F",
                "aliases": ["gold", "oro", "xau"],
                "name": "Gold"},
    "silver":  {"symbol": "SI=F",  "futures": "SI=F",
                "aliases": ["silver", "plata", "xag"],
                "name": "Silver"},
    "oil":     {"symbol": "CL=F",  "futures": "CL=F",
                "aliases": ["oil", "crude", "wti", "petróleo", "petroleo", "brent"],
                "name": "Oil"},
}

MIN_EDGE = 0.04  # 4% base — bajado de 6% (4-Jun): con WR 73% en target-based,
                 # el modelo tiene señal real. 6% era demasiado estricto y bloqueaba
                 # todo porque los mercados eficientes tienen spread de solo 3-5%.
# Daily intraday "Up or Down on" requiere edge mayor por mala calibración
# (4-May: 4/4 LOST, 5-May: 0/6 LOST). Subido a 10% el 7-May-2026.
# Bajado a 8% el 15-May-2026: con Sonnet IA + ATM trap + anti-señal cola
# las defensas están en capas; el 10% rechazaba edges legítimos de 6-9%
# (AMZN finish week 14-May, edge NO 6.4% no se ejecutó).
# Subido a 12% el 1-Jun-2026: 27 daily-intraday a 8%, 30% WR, -$57.12.
# Coin-flip estructural; con whitelist tech-only el threshold debe subir.
MIN_EDGE_DAILY_INTRADAY = 0.12
# Anti-señal: en colas (precio ≤0.20 o ≥0.80) un edge enorme suele ser
# error de modelo, no oportunidad. Histórico (32 trades): perdedoras
# avg edge 30.9% vs ganadoras 13.7%. Casos: TSLA $400 @0.215 edge 61% LOST,
# Gold @0.105 edge 59% LOST. Si cumple ambas condiciones → skip.
EXTREME_PRICE_LOW = 0.20
EXTREME_PRICE_HIGH = 0.80
EXTREME_EDGE_CAP = 0.30
# Cap absoluto de edge (1-Jun-2026): 9 trades con edge>=35% en precios
# medios → 1W/8L (-$42.36). El cap de cola solo cubre extremos; edges
# >40% en cualquier precio son anti-señal del modelo, no oportunidad.
ABSOLUTE_EDGE_CAP = 0.40
# Sizing inverso: edges >25% históricamente son anti-señal,
# reducir stake a la mitad para limitar daño.
HIGH_EDGE_SIZING_THRESHOLD = 0.25

# Whitelist tradeable (1-Jun-2026): solo tickers con WR positivo en n=99.
# AAPL 4/4 100%, GOOGL 3/5 60% +$10.69, NVDA 5/7 71% +$8.97, META 2/3 67% +$4.53.
# TSLA 4/8 50% break-even (marginal, dentro). AMZN 1/2 50% -$3.74 (n bajo).
# MSFT n=0 (sin trades, dentro por consistencia tech).
# BANEADOS: WTI 2/10 -$45.91, DJIA 0/3, SPY 0/3, RUT 0/1, NFLX 0/1, commodities.
# sp500/nasdaq se quedan en INDICES como benchmark macro pero NO son tradeables.
TRADEABLE_TICKERS = {"nvda", "googl", "aapl", "tsla", "meta", "amzn", "msft"}

# Conviction tiers (2-Jun-2026): concentrar el riesgo en tickers con WR probado.
# Tier A (full stake): NVDA 5/7 71% +$8.97, GOOGL 3/5 60% +$10.69,
#   AAPL 4/4 100% +$11.31, META 2/3 67% +$4.53.
# Tier B (marginal/sin data → 0.7x): TSLA 4/8 50% -$1.22, AMZN 1/2 -$3.74, MSFT n=0.
# NO sube el stake de nadie; solo recorta los flojos. Reasignación, no aumento.
PROVEN_WINNERS = {"nvda", "googl", "aapl", "meta"}


class StockTrader:
    """Estrategia de trading en mercados bursátiles de Polymarket."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache = {}
        self.cache_ttl = 120    # 2 min (mercado se mueve rápido)
        self.last_run = 0
        self.min_interval = 180  # 3 min entre escaneos
        self.traded_markets = set()
        # Tickers apostados HOY con su dirección para evitar
        # correlación negativa (apostar UP y DOWN del mismo activo
        # el mismo día = garantizado perder uno).
        # Formato: {"date": "YYYY-MM-DD", "data": {"amzn": {"UP"}, ...}}
        self._today_directions: Dict = {"date": "", "data": {}}
        # Tope diario de apuestas de stocks para limitar riesgo de
        # correlación de mercado (17-Abr: 5 stocks Up el mismo día,
        # todos perdieron −$42.84 cuando la bolsa bajó).
        self._daily_stock_count: Dict = {"date": "", "count": 0}
        self._daily_limit_reached = False
        # Daily loss limit: si perdimos $15+ hoy, pausar stocks resto
        # del día. SPORTS sigue (baja varianza). Ref: 21-Abr -$38 stocks.
        self._daily_loss_check: Dict = {"date": "", "start_balance": 0.0}
        from modules.news_monitor import NewsMonitor
        self.news = NewsMonitor()
        # IA como último filtro (29-Abr): si hay API key, Claude Sonnet
        # confirma cada apuesta antes de ejecutar. Si no hay key, opera
        # solo con filtros base (estado pre-29-Abr).
        self.ai = None
        if os.getenv("ANTHROPIC_API_KEY"):
            try:
                from core.ai_analyzer import AIAnalyzer
                self.ai = AIAnalyzer()
                logger.info("📈 Stock trader: IA (Claude Sonnet) activada como último filtro")
            except Exception as e:
                logger.warning(f"📈 Stock trader: IA no inicializó ({e}) — operando sin IA")
        self._load_traded()
        self._load_today_directions()

    def _register_bet_direction(self, index_key: str, direction: str):
        """Registra la dirección apostada para un ticker en el día."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._today_directions["date"] != today:
            self._today_directions = {"date": today, "data": {}}
        if index_key not in self._today_directions["data"]:
            self._today_directions["data"][index_key] = set()
        self._today_directions["data"][index_key].add(direction.upper())
        self._save_today_directions()

    def _save_today_directions(self):
        # Persistir a disco: sin esto, un restart borra el estado y el
        # filtro de correlación negativa deja pasar la apuesta opuesta.
        # Bug real 24-Abr: MSFT DOWN 14:05 → restart 14:29 → MSFT YES 14:30.
        try:
            os.makedirs("data", exist_ok=True)
            serializable = {
                "date": self._today_directions["date"],
                "data": {
                    k: sorted(list(v))
                    for k, v in self._today_directions["data"].items()
                },
            }
            with open("data/today_directions.json", "w") as f:
                json.dump(serializable, f, indent=2)
        except Exception as e:
            logger.warning(f"No se pudo persistir today_directions: {e}")

    def _load_today_directions(self):
        try:
            with open("data/today_directions.json", "r") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        today = datetime.now().strftime("%Y-%m-%d")
        if raw.get("date") != today:
            return
        self._today_directions = {
            "date": raw["date"],
            "data": {k: set(v) for k, v in raw.get("data", {}).items()},
        }

    def _is_already_bet(self, index_key: str) -> bool:
        """
        ¿Ya apostamos CUALQUIER dirección de este ticker HOY?

        Max 1 apuesta por ticker por día. Bloquea tanto direcciones
        opuestas (Google Up + Google Down → -EV garantizado, observado
        14-Abr) como múltiples apuestas en la misma dirección
        (ej: TSLA Up or Down + TSLA finish week above $380 = correlación
        positiva pero exposición duplicada al mismo riesgo idiosincrático).

        Caso real 30-Abr: TSLA Up/Down + TSLA finish week, WTI $105 +
        WTI $107 — el bot tomó 2 apuestas del mismo ticker en el día.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if self._today_directions["date"] != today:
            return False
        existing = self._today_directions["data"].get(index_key, set())
        return bool(existing)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _load_traded(self):
        try:
            with open("data/bets_placed.json", "r") as f:
                data = json.load(f)
                self.traded_markets = set(data.get("market_ids", []))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_bet(self, market_id: str, question: str = ""):
        try:
            os.makedirs("data", exist_ok=True)
            try:
                with open("data/bets_placed.json", "r") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {"market_ids": [], "history": []}
            if market_id and market_id not in data["market_ids"]:
                data["market_ids"].append(market_id)
                data["history"].append({
                    "market_id": market_id, "question": question,
                    "timestamp": datetime.now().isoformat(),
                    "strategy": "STOCKS"
                })
            with open("data/bets_placed.json", "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # PUNTO DE ENTRADA
    # ═══════════════════════════════════════════════════════════════

    async def run_cycle(self) -> Optional[Dict]:
        """Busca y tradea mercados de índices bursátiles."""
        if STATE.is_paused:
            return None

        now = time.time()
        if now - self.last_run < self.min_interval:
            return None
        self.last_run = now

        logger.info("📈 Stock Trader: Buscando mercados de bolsa...")

        stock_markets = await self._find_stock_markets()
        if not stock_markets:
            logger.info("   📈 No se encontraron mercados de bolsa")
            return None

        logger.info(f"   📈 Encontrados {len(stock_markets)} mercados de bolsa")

        for market in stock_markets:
            try:
                trade = await self._analyze_and_trade(market)
                if trade:
                    return trade
            except Exception as e:
                logger.error(f"   📈 Error: {e}")

        logger.info("   📈 Sin oportunidades de bolsa en este ciclo")
        return None

    # ═══════════════════════════════════════════════════════════════
    # BUSCAR MERCADOS
    # ═══════════════════════════════════════════════════════════════

    async def _find_stock_markets(self) -> List[Dict]:
        """Busca mercados de índices bursátiles activos."""
        session = await self._get_session()

        # Keywords EXCLUSIVOS de bolsa — índices + acciones individuales
        stock_keywords = [
            # Índices
            "s&p", "s&p 500", "sp500", "spx", "spy",
            "nasdaq", "ndx", "qqq",
            "dow jones", "djia", "dia",
            "russell 2000", "rut", "iwm",
            "stock market",
            # Acciones individuales (Mag 7 + populares)
            "nvidia", "nvda",
            "google", "googl", "alphabet",
            "apple", "aapl",
            "tesla", "tsla",
            "meta", "facebook",
            "amazon", "amzn",
            "microsoft", "msft",
            "netflix", "nflx",
            # Commodities
            "gold", "oro", "xau", "silver", "plata", "xag",
            "oil", "crude", "wti", "petróleo", "petroleo", "brent",
            # Frases comunes de mercado
            "close up", "close down", "close green", "close red",
            "opens up", "opens down",
            "trading day", "hit (high)", "hit (low)"
        ]

        # Excluir crypto para evitar falsos positivos
        crypto_exclude = ["btc", "bitcoin", "eth", "ethereum", "sol", "solana",
                          "bnb", "xrp", "doge", "crypto", "token", "coin"]

        # Exigir keyword direccional (agregado 14-Apr): evita que mercados
        # como "Will Netflix beat quarterly earnings?" pasen el filtro de
        # stock_keywords (hacía matchear por "netflix") y consuman llamadas
        # a Yahoo Finance para nada.
        directional_req = [
            "up or down", "up/down", "opens up", "opens down",
            "close above", "close below", "close up", "close down",
            "close green", "close red", "trading day", "above $", "below $",
        ]

        # NOTA (29-Abr-2026): Gamma API rompe (HTTP 500) cuando se pasan
        # `end_date_min`/`end_date_max`. Los quitamos y filtramos endDate
        # localmente más abajo (línea ~334). Mientras tanto paginamos más
        # profundo por volumen para alcanzar los daily.
        markets = []
        for offset in [0, 100, 200, 300, 400, 500, 600, 700, 800, 900]:
            try:
                async with session.get(
                    f"{GAMMA_API_URL}/markets",
                    params={
                        "active": "true", "closed": "false",
                        "limit": 100, "offset": str(offset),
                        "order": "volume", "ascending": "false",
                    }
                ) as resp:
                    if resp.status == 200:
                        batch = await resp.json()
                        if not batch:
                            break
                        for m in batch:
                            q = (m.get("question") or "").lower()

                            # Excluir mercados crypto
                            if any(kw in q for kw in crypto_exclude):
                                continue

                            # Exigir keyword direccional además de ticker
                            if not any(kw in q for kw in directional_req):
                                continue

                            if any(kw in q for kw in stock_keywords):
                                mid = str(m.get("id", ""))
                                cid = m.get("conditionId", "")
                                if mid in self.traded_markets or cid in self.traded_markets:
                                    continue

                                # Ventana de resolución: 48h para diarios,
                                # 120h para semanales ("finish week/above/below").
                                # Los semanales (73% WR, +$26.73) resuelven el
                                # viernes y desde lunes/martes están a >48h —
                                # antes eran bloqueados todos los lun-mié sin
                                # razón. 120h = 5 días cubre la semana completa.
                                end_str = m.get("endDate", "")
                                if end_str:
                                    try:
                                        end_dt = datetime.fromisoformat(
                                            end_str.replace("Z", "+00:00"))
                                        hours = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                                        q_lower_end = q  # ya está en lower
                                        is_weekly_market = any(kw in q_lower_end for kw in (
                                            "finish week", "finish above", "finish below",
                                            "end above", "end below",
                                        ))
                                        max_hours = 120 if is_weekly_market else 48
                                        if hours < 0 or hours > max_hours:
                                            continue
                                    except:
                                        pass

                                markets.append(m)
            except Exception:
                break

        commodity_count = sum(1 for m in markets
            if any(kw in (m.get("question","") or "").lower()
                   for kw in ["gold","silver","oil","crude","wti"]))
        if commodity_count > 0:
            logger.info(f"   📈 {commodity_count} mercados de commodities")

        return markets

    # ═══════════════════════════════════════════════════════════════
    # ANALIZAR Y TRADEAR
    # ═══════════════════════════════════════════════════════════════

    async def _analyze_and_trade(self, market: Dict) -> Optional[Dict]:
        """Analiza mercado de bolsa y ejecuta si hay edge."""
        # Tope de 2 stock bets/día: con mercado bajista (20-Abr: balance
        # cayó a $135, 5 bets Up perdieron juntas −$34). Bajado de 5→2
        # para limitar pérdida peor caso a ~−$15.
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_stock_count["date"] != today:
            self._daily_stock_count = {"date": today, "count": 0}
        if self._daily_stock_count["count"] >= 4:
            # Override por edge alto retirado (7-May-2026): la data muestra
            # que perdedoras tienen avg edge 30.9% vs ganadoras 13.7% — los
            # edges enormes son anti-señal, no oportunidad. El cap diario
            # ahora es duro y se aplica abajo después de calcular edge.
            logger.info(f"      ⛔ Max 4 stock bets/día alcanzado — skip resto del día")
            self._daily_limit_reached = True
        else:
            self._daily_limit_reached = False

        # Daily loss limit: si perdimos $35+ hoy, pausar stocks resto del
        # día. Motivación: 21-Abr stocks perdieron ~$38 consecutivos sin freno.
        # Subido de -$25 a -$35 (2-May-2026): el límite mide solo balance
        # líquido; cuando hay posiciones ganadoras pendientes el "loss" es
        # falso. Más margen evita disparos prematuros.
        if self._daily_loss_check["date"] != today:
            self._daily_loss_check = {
                "date": today,
                "start_balance": STATE.current_bankroll,
            }
        net_daily_pnl = STATE.current_bankroll - self._daily_loss_check["start_balance"]
        if net_daily_pnl <= -35:
            logger.warning(
                f"      ⛔ Daily loss limit NETO: P&L hoy ${net_daily_pnl:.2f} "
                f"(límite -$35). Stocks pausados hasta mañana."
            )
            try:
                import os
                from modules.telegram_monitor import TelegramMonitor
                tg = TelegramMonitor()
                if tg.enabled:
                    import asyncio
                    asyncio.ensure_future(tg.send(
                        f"🚨 DAILY LOSS LIMIT\n"
                        f"P&L neto hoy: ${net_daily_pnl:.2f}\n"
                        f"Límite: -$35\n"
                        f"Stocks PAUSADOS hasta mañana."
                    ))
            except Exception:
                pass
            return None
        elif net_daily_pnl < -22:
            logger.info(
                f"      ⚠️ P&L neto hoy: ${net_daily_pnl:.2f} "
                f"(acercándose al límite -$35)"
            )

        # Solo apostar stocks durante horario de mercado US.
        # Pre-market (antes 9:30 ET) tiene datos poco confiables:
        # el 20-Abr apostó 3 stocks a las 8:44 UTC y SPX Opens Up
        # abrió DOWN → −$8.69 inmediato.
        # Mercado US: 9:30-16:00 ET = 13:30-20:00 UTC
        # Ventana: 14:00-20:59 UTC (30 min buffer tras open).
        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday()  # 0=lunes, 5=sábado, 6=domingo
        market_hour = now_utc.hour

        # Mercado US cerrado fines de semana — datos de Yahoo serían
        # del viernes y se apostaría con info stale (bug 26-Abr: 2 stocks
        # apostados un domingo con datos del viernes).
        if weekday >= 5:
            logger.info(f"      ⏰ Fin de semana (día {weekday}): stocks cerrado")
            return None

        # Mercado US solo 14:00-20:00 UTC en días hábiles
        if market_hour < 14 or market_hour > 20:
            logger.info(f"      ⏰ Fuera de horario US ({now_utc.strftime('%H:%M')} UTC): skip")
            return None

        question = market.get("question", "")
        market_id = str(market.get("id", ""))

        # 1. Parsear pregunta
        parsed = self._parse_stock_question(question)
        if not parsed:
            return None

        index_key = parsed["index"]
        direction = parsed["direction"]

        # Whitelist (1-Jun-2026): solo tradear tickers con WR histórico
        # positivo. Bloquea WTI (-$45.91), índices puros (DJIA/SPY/RUT 0/7),
        # commodities. sp500/nasdaq se usan como benchmark, no como apuesta.
        if index_key not in TRADEABLE_TICKERS:
            logger.info(
                f"      ⛔ {INDICES[index_key]['name']} fuera de whitelist "
                f"(recovery 1-Jun): skip"
            )
            return None

        # Identificar mercado "Up or Down on [today]" (intradía).
        # Track record 4-5 May 2026: 0/6 LOST (-$48) por mean-reversion.
        # Los semanales "close above/below $X" siguen sanos (GOOGL +$6.14, etc.).
        is_daily_intraday = "up or down on" in question.lower()

        logger.info(f"   📈 {question[:55]}")

        # 2-Jun-2026: "Up or Down" intradía DESACTIVADO. Análisis de n=27:
        # 30% WR, -$57.12 (=98% de la pérdida total de stocks), y el edge está
        # INVERTIDO (perdidas edge medio 0.21 > ganadas 0.11) → el modelo no
        # tiene señal direccional sin un precio objetivo, solo ruido sobre-
        # confiado. Los target-based (closes above $X / finish week) van 73% WR.
        if is_daily_intraday and not getattr(SAFETY, 'enable_intraday_updown', False):
            logger.info(
                f"      ⛔ 'Up or Down' intradía DESACTIVADO (2-Jun): 30% WR, "
                f"-$57 histórico, edge anti-señal. Solo target-based markets."
            )
            return None

        # Filtro VIX: volatilidad del mercado. VIX alto = mercado en pánico,
        # los stocks tienden a comportamiento errático y los filtros de
        # tendencia no calibran. 22-Abr: agregado tras 4/4 LOSS del 21-Abr.
        vix = await self._get_vix()
        if vix is not None:
            logger.info(f"      📊 VIX: {vix:.1f}")
            if vix > 30:
                logger.warning(f"      ⚠️ VIX {vix:.1f} > 30 (pánico): skip")
                return None
            elif vix > 25:
                logger.info(f"      📊 VIX {vix:.1f} > 25 (nervioso): skip")
                return None
            elif vix > 20:
                logger.info(f"      ⚠️ VIX {vix:.1f} elevado pero operando")
        else:
            logger.warning(f"      ⚠️ VIX no disponible — continuar con precaución")

        # Obtener S&P trend y news sentiment para usar más abajo.
        # IMPORTANTE (5-May-2026): el bloqueo direction-dependiente se movió a
        # DESPUÉS de calcular `effective_direction`. Antes usaba `direction.upper()`
        # que para mercados "Up or Down" siempre es "up" (parser default), así
        # que el filtro NO bloqueaba apuestas DOWN (side=NO). 3 de las 6 perdidas
        # de Lun-Mar (WTI, NVDA, Silver) habrían sido bloqueadas por este filtro
        # si hubiera usado effective_direction. Fix: separar fetch (aquí) del
        # bloqueo (post side selection).
        # Fail-safe: si no se puede obtener S&P data, skip (no apostar ciego).
        try:
            sp500_data = await self._get_market_data("sp500")
            if sp500_data is None:
                logger.warning(f"      ⚠️ No se pudo obtener S&P data — skip por precaución")
                return None
            market_change = sp500_data.get("change_pct", 0)
            logger.info(f"      📊 S&P tendencia: {market_change:+.2%}")
        except Exception as e:
            logger.warning(f"      ⚠️ Error trend check: {e} — skip por precaución")
            return None

        try:
            news = self.news.get_sentiment()
            logger.info(f"      📰 News: {news['sentiment']} (score {news['score']:+d})")
        except Exception as e:
            logger.debug(f"      News check error: {e}")
            news = {"sentiment": "NEUTRAL", "score": 0}

        # 2. Obtener datos del mercado. Si el mercado nombra explícitamente un
        # ETF (SPY/QQQ/DIA/IWM), usar el precio del ETF — el índice subyacente
        # (^GSPC etc.) tiene precio ~10x mayor y confunde al modelo y a Sonnet.
        etf_symbol = parsed.get("etf_symbol")
        mkt_data = await self._get_market_data(index_key, override_symbol=etf_symbol)
        if not mkt_data:
            logger.info(f"      No se pudo obtener datos de {INDICES[index_key]['name']}")
            return None
        if etf_symbol:
            logger.info(f"      🔁 Usando ETF {etf_symbol} (no índice) para precio del mercado")

        # Gap filter (17-Abr): "close above $X" / "close below $X" con target
        # lejano pierden 3/3 esta semana (AAPL >$255, AMZN >$250, NVDA >$200).
        # Up/Down simples mantienen 90% WR.
        # Ext 23-Abr: mercados semanales ("finish week above $X") también —
        # META $690 pasó con gap real 3.45% (-$9.22). Umbral 5% para semanal.
        import re as _re
        q_lower = question.lower()
        target_match = _re.search(r'\$(\d+(?:,\d{3})*(?:\.\d+)?)', question)
        # 13-May-2026: aceptar "close above/below" y "closes above/below"
        # (plural). SPY/WTI mercados usan "closes above $X" y el filtro de gap
        # no se disparaba, dejando pasar bets at-the-money que el modelo
        # sobreestimaba (SPY $740 +47.5% edge LOST -$3.54, WTI $98 LOST -$7.07).
        daily_kw = bool(_re.search(r'\bcloses?\s+(above|below)\b', q_lower))
        weekly_kw = any(kw in q_lower for kw in (
            "finish week", "finish above", "finish below",
            "end above", "end below",
        ))
        if target_match and (daily_kw or weekly_kw):
            try:
                target_price = float(target_match.group(1).replace(",", ""))
                current_price = mkt_data.get("price", 0)
                if current_price > 0:
                    is_weekly = weekly_kw and not daily_kw
                    # Gap asimétrico (9-Jun-2026): el riesgo no es el mismo
                    # si el precio ya superó el target que si aún necesita subir.
                    #
                    # Caso A — precio YA por encima del target (bet YES fácil):
                    #   NVDA $1,100 vs target $1,050 → solo necesita no caer 4.5%
                    #   → gap máximo 12% diario / 20% semanal (nunca bloquear de facto)
                    #
                    # Caso B — precio por DEBAJO del target (stock necesita subir):
                    #   NVDA $1,000 vs target $1,050 → necesita subir 5%
                    #   → gap máximo 3% diario / 5% semanal (estricto, como antes)
                    #
                    # Historial: las pérdidas originales (AAPL >$255, AMZN >$250,
                    # NVDA >$200) eran todas Caso B — stock debajo del target.
                    needs_to_climb = target_price > current_price
                    if needs_to_climb:
                        max_gap = 0.05 if is_weekly else 0.03  # estricto
                    else:
                        max_gap = 0.20 if is_weekly else 0.12  # permisivo (cola larga lo filtra)
                    gap_pct = abs(target_price - current_price) / current_price
                    if gap_pct > max_gap:
                        kind = "semanal" if is_weekly else "diario"
                        direction_note = "subir" if needs_to_climb else "no caer"
                        logger.info(
                            f"      Gap {gap_pct:.1%} > {max_gap:.0%} para "
                            f"{kind} (necesita {direction_note} ${abs(target_price-current_price):.0f}): skip"
                        )
                        return None
            except (ValueError, AttributeError):
                pass

        change = mkt_data.get("change_pct", 0)
        _label = etf_symbol if etf_symbol else INDICES[index_key]['name']
        logger.info(f"      {_label}: ${mkt_data['price']:,.2f} | "
                     f"Cambio: {change:+.2%} | Estado: {mkt_data.get('state', '?')}")

        # At-the-money trap (13-May-2026, ampliado 1-Jun-2026): cuando el
        # precio está pegado al target (<1%) Y ya hubo un movimiento del día
        # (>1.5%), zona de mean-reversion. El modelo P(up) sobreestima
        # (cap 78-82%) cuando la realidad es ~coin flip. Casos 11-May:
        #   SPY $740 (precio $740, +2.98%) → bot P_up=78.5% → LOST -$3.54
        #   WTI $98  (precio $98,  +3.16%) → bot P_up=79.1% → LOST -$7.07
        # Umbral 0.5%→1% y change 2%→1.5% para cubrir más casos cerca del strike.
        if target_match and (daily_kw or weekly_kw):
            try:
                _tp = float(target_match.group(1).replace(",", ""))
                _cp = mkt_data.get("price", 0) or 0.001
                _atm_gap = abs(_tp - _cp) / _cp
                if _atm_gap < 0.01 and abs(change) > 0.015:
                    logger.info(
                        f"      🪤 At-the-money trap: precio ${_cp:.2f} ≈ target "
                        f"${_tp:.2f} ({_atm_gap:.2%}) con cambio {change:+.2%} "
                        f"hoy → skip (mean-reversion likely)"
                    )
                    return None
            except (ValueError, AttributeError):
                pass

        # 3. Calcular probabilidad
        prob_direction = self._calculate_prob(mkt_data, direction, parsed.get("threshold_pct"))
        logger.info(f"      P({direction})={prob_direction:.1%}")

        # 4. Comparar con mercado
        outcomes = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str):
            prices = json.loads(outcomes)
        else:
            prices = outcomes
        if len(prices) < 2:
            return None

        yes_price = float(prices[0])
        no_price = float(prices[1])

        # VALIDACIÓN: rechazar precios inválidos (precio = 0 o = 1 exacto:
        # mercado cerrado o sin liquidez).
        if yes_price <= 0.0 or yes_price >= 1.0 or no_price <= 0.0 or no_price >= 1.0:
            logger.info(f"      Precios inválidos (YES={yes_price:.2f}, NO={no_price:.2f}), skip")
            return None

        # Extreme-side bet (15-May): si una side está a ≤$0.05 y la otra ≥$0.95,
        # ANTES skip. Ahora: si nuestro P(side cara) ≥80%, permitir apostar
        # la side cara con stake reducido (alto WR esperado, payout chico pero
        # casi seguro). Si NO, mantener skip — apostar la side barata con prob
        # real ~20% es trampa.
        _extreme_side_bet = False
        if (yes_price <= 0.05 or yes_price >= 0.95
                or no_price <= 0.05 or no_price >= 0.95):
            # Determinar cuál side es la cara (≥0.95)
            cara_side = "YES" if yes_price >= 0.95 else "NO"
            cara_prob = prob_direction if cara_side == "YES" else (1 - prob_direction)
            if cara_prob >= 0.80:
                _extreme_side_bet = True
                logger.info(
                    f"      ⚡ Extreme-side bet: {cara_side}@{(yes_price if cara_side=='YES' else no_price):.2f} "
                    f"(prob real {cara_prob:.0%}≥80%), stake reducido"
                )
            else:
                logger.info(
                    f"      Cola extrema (YES={yes_price:.2f}, NO={no_price:.2f}) y "
                    f"prob {cara_prob:.0%}<80%: skip"
                )
                return None

        tokens = market.get("clobTokenIds", "[]")
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if len(tokens) < 2:
            return None

        # El mercado pregunta "will X close up/down?"
        # prob_direction = probabilidad de que el mercado se mueva en 'direction'
        # Si direction=up → prob_direction = P(YES)
        edge_yes = prob_direction - yes_price
        edge_no = (1 - prob_direction) - no_price

        # Threshold diferenciado: daily intraday tiene calibración pobre
        # → exigir más edge. Otros formatos (close above/below, finish week)
        # mantienen el threshold base.
        min_edge_required = MIN_EDGE_DAILY_INTRADAY if is_daily_intraday else MIN_EDGE

        if _extreme_side_bet:
            # En extreme-side, forzamos apostar la side cara (≥0.95) con
            # prob real ≥80% — edge probablemente negativo pero alto WR.
            cara_side = "YES" if yes_price >= 0.95 else "NO"
            if cara_side == "YES":
                side, edge, price, token_id = "YES", edge_yes, yes_price, tokens[0]
            else:
                side, edge, price, token_id = "NO", edge_no, no_price, tokens[1]
        elif edge_yes > edge_no and edge_yes >= min_edge_required:
            side, edge, price, token_id = "YES", edge_yes, yes_price, tokens[0]
        elif edge_no >= min_edge_required:
            side, edge, price, token_id = "NO", edge_no, no_price, tokens[1]
        else:
            label = "daily intraday" if is_daily_intraday else "base"
            logger.info(
                f"      Edge YES={edge_yes:+.1%}, NO={edge_no:+.1%} → "
                f"insuficiente (req {min_edge_required:.0%} {label})"
            )
            return None

        # Cap absoluto de edge (1-Jun-2026): edges >40% en CUALQUIER precio
        # son anti-señal del modelo. El cap EXTREME_EDGE_CAP=30% solo aplica
        # en colas; pero en precios medios 9 trades con edge>=35% → 1W/8L
        # (-$42.36). El modelo "se emociona" en el extremo de confianza.
        # Excepción: extreme_side_bet (prob_real ≥80%, payout chico) ya tiene
        # su propio sizing reducido.
        if not _extreme_side_bet and edge > ABSOLUTE_EDGE_CAP:
            logger.info(
                f"      🚫 Edge {edge:.1%} > {ABSOLUTE_EDGE_CAP:.0%} "
                f"(anti-señal modelo en cualquier precio): skip"
            )
            return None

        # Daily limit: skip extra trades. OVERRIDE retirado (7-May): la data
        # mostró que edges >25% son anti-señal (perdedoras avg 30.9% edge),
        # así que dejar de premiar edges altos que en realidad son ruido.
        if getattr(self, '_daily_limit_reached', False):
            logger.info(f"      ⛔ Max daily alcanzado, skip (edge {edge:.1%})")
            return None

        # Colas largas (precio < 10¢ o > 90¢) tienen alta varianza y
        # poco upside realista. El único STOCKS LOST histórico fue
        # SPX Up/Down @ $0.060 → −$7.50 (14-Abr). Más estricto que
        # el filtro 0.02/0.98 que ya existe arriba.
        # Excepción: extreme-side bet (15-May) apuesta deliberadamente la
        # side cara cuando prob_real ≥80% — no es cola larga adversa.
        if not _extreme_side_bet and (price < 0.10 or price > 0.90):
            logger.info(f"      Cola larga @ {price:.3f}: skip")
            return None

        # Anti-señal: precio extremo + edge enorme = modelo equivocado,
        # no oportunidad. El mercado en colas suele tener info que el bot
        # no captura. Casos 4-5 May: TSLA $400 @0.215 edge 61% LOST,
        # Gold @0.105 edge 59% LOST.
        if (price <= EXTREME_PRICE_LOW or price >= EXTREME_PRICE_HIGH) and edge > EXTREME_EDGE_CAP:
            logger.info(
                f"      🚫 Cola {price:.3f} con edge {edge:.1%}>30% (anti-señal): skip"
            )
            return None

        # Determinar la dirección EFECTIVA que estamos apostando:
        # - Mercado "Up or Down" con YES → bet UP (direction ya es UP)
        # - Mercado "Up or Down" con NO → bet DOWN (opuesto a direction)
        # - Mercado "close above $X" con YES → bet UP
        # - Mercado "close above $X" con NO → bet DOWN
        effective_direction = direction.upper()
        if side == "NO":
            effective_direction = "DOWN" if effective_direction == "UP" else "UP"

        # Filtro tendencia S&P (post side selection — usa effective_direction).
        # 20-Abr: mercado -2%, 5 bets Up perdieron -$34. Umbral ±0.3% (29-Abr).
        # Bug fix 5-May: antes usaba parser direction, fallaba para "Up or Down"
        # con side=NO (3/6 perdedoras Lun-Mar habrían sido bloqueadas).
        if market_change < -0.003 and effective_direction == "UP":
            logger.info(
                f"      📉 Mercado bajando ({market_change:+.2%}), "
                f"skip bet UP en {INDICES[index_key]['name']}"
            )
            return None
        if market_change > 0.003 and effective_direction == "DOWN":
            logger.info(
                f"      📈 Mercado subiendo ({market_change:+.2%}), "
                f"skip bet DOWN en {INDICES[index_key]['name']}"
            )
            return None

        # Filtro news sentiment: solo bloquear si score MUY extremo (<=-5 o >=5)
        # y S&P no contradice. Score -3/-4 = ruido normal de noticias, no señal.
        # 4-Jun: bajado de -3 a -5 porque score -3 bloqueaba TODOS los UP y el
        # contador de Telegram no lo mostraba → 0 apuestas invisibles.
        if news["score"] <= -5 and effective_direction == "UP":
            if market_change > 0.002:
                logger.info(
                    f"      📰 News muy bearish (score {news['score']:+d}) PERO "
                    f"S&P real {market_change:+.2%} UP → confiar en mercado"
                )
            else:
                logger.info(f"      📰 Noticias muy bearish ({news['score']:+d}), skip UP")
                return None
        if news["score"] >= 5 and effective_direction == "DOWN":
            if market_change < -0.002:
                logger.info(
                    f"      📰 News muy bullish (score {news['score']:+d}) PERO "
                    f"S&P real {market_change:+.2%} DOWN → confiar en mercado"
                )
            else:
                logger.info(f"      📰 Noticias muy bullish ({news['score']:+d}), skip DOWN")
                return None

        # Anti-continuación retirado el 15-May-2026.
        # Razón: bloqueaba apuestas legítimas cuando había momentum genuino
        # (14-May AAPL +3.73% con S&P +2.35% → bot skip UP y la apuesta
        # hubiera ganado). El caso 11-May SPY $740 que motivó el filtro está
        # cubierto por el ATM trap (precio≈target + cambio>2%), y Sonnet IA
        # valida momentum vs mean-reversion con contexto live.

        # Max 1 apuesta por ticker por día — bloquea cualquier duplicado,
        # no solo direcciones opuestas. Evita TSLA Up/Down + TSLA finish
        # week, WTI $105 + WTI $107 (caso real 30-Abr).
        if self._is_already_bet(index_key):
            existing = self._today_directions["data"].get(index_key, set())
            logger.info(
                f"      ⛔ Skip: {INDICES[index_key]['name']} ya "
                f"apostado HOY en {existing} (max 1 apuesta/ticker/día)"
            )
            return None

        logger.info(f"      🎯 EDGE {side}: {edge:.1%}")

        # IA como ÚLTIMO filtro (29-Abr): Claude Sonnet revisa todos los filtros
        # base ya pasados y vetea si ve un catalizador adverso o riesgo de fade.
        # Conservador: si IA no responde, continuamos con filtros base; si IA
        # discrepa del side, también skip (la IA tiene contexto que el bot no).
        if self.ai:
            try:
                from core.market_scanner import MarketOpportunity
                hours_left = max(
                    (datetime.fromisoformat((market.get("endDate") or "").replace("Z", "+00:00"))
                     - datetime.now(timezone.utc)).total_seconds() / 3600,
                    0.0
                ) if market.get("endDate") else 24.0

                # 30-Abr: Sonnet alucinaba precios viejos de su training data
                # (Ene-2026) cuando le faltaban datos reales. Inyectamos el
                # contexto live aquí para que NO adivine: ticker, precio actual,
                # target, S&P trend, VIX, edge calculado, news sentiment.
                _local = locals()
                _effective_symbol = etf_symbol if etf_symbol else INDICES[index_key]['symbol']
                _ctx_lines = [
                    "LIVE DATA from today (override any training-data prices):",
                    f"- Ticker: {INDICES[index_key]['name']} ({_effective_symbol})",
                    f"- Current price: ${mkt_data.get('price', 0):,.2f}",
                    f"- Change today: {change:+.2%}",
                ]
                if _local.get("target_price") is not None:
                    _ctx_lines.append(f"- Market target: ${_local['target_price']:,.2f}")
                if _local.get("gap_pct") is not None:
                    _ctx_lines.append(f"- Gap to target: {_local['gap_pct']:.1%}")
                _ctx_lines.append(f"- S&P 500 today: {market_change:+.2%}")
                if vix is not None:
                    _ctx_lines.append(f"- VIX: {vix:.1f}")
                _ctx_lines.append(f"- Bot edge: {edge:.1%} on {side} (P_dir={prob_direction:.1%})")
                _news = _local.get("news") or {}
                if _news:
                    _ctx_lines.append(
                        f"- News sentiment: {_news.get('sentiment', '?')} "
                        f"(score {_news.get('score', 0):+d})"
                    )
                real_context = "\n".join(_ctx_lines)

                opp = MarketOpportunity(
                    market_id=market_id,
                    condition_id=str(market.get("conditionId", "") or ""),
                    question=question,
                    description=real_context,
                    category="stocks",
                    outcome_yes_price=yes_price,
                    outcome_no_price=no_price,
                    liquidity=float(market.get("liquidityNum") or 0),
                    volume=float(market.get("volumeNum") or 0),
                    volume_24h=float(market.get("volume24hr") or 0),
                    end_date=market.get("endDate", "") or "",
                    token_id_yes=tokens[0] if tokens else "",
                    token_id_no=tokens[1] if len(tokens) > 1 else "",
                    slug=market.get("slug", "") or "",
                    active=True,
                    days_until_resolution=max(int(hours_left / 24), 0),
                    hours_until_resolution=hours_left,
                )
                ai_analysis = await self.ai.analyze_market(opp)
                if ai_analysis is None:
                    logger.warning(f"      ⚠️ IA no respondió — continuar con filtros base")
                elif ai_analysis.recommended_action == "SKIP":
                    logger.info(f"      🧠 IA dice SKIP: {ai_analysis.reasoning[:100]}")
                    return None
                elif ai_analysis.side.upper() != side:
                    logger.info(
                        f"      🧠 IA discrepa side: bot={side}, IA={ai_analysis.side} "
                        f"({ai_analysis.reasoning[:80]}) — skip"
                    )
                    return None
                else:
                    # Para daily intraday: requerir confidence ≥ medium (0.65).
                    # Las 6 perdedoras 4-5 May tuvieron Sonnet aprobando con
                    # razonamientos de "momentum continúa" — confidence alta no
                    # se correlacionó con éxito, pero confidence baja debe ser
                    # razón inmediata de skip para este bucket riesgoso.
                    if is_daily_intraday and ai_analysis.confidence < 0.65:
                        logger.info(
                            f"      🧠 IA confidence {ai_analysis.confidence:.2f} < 0.65 "
                            f"para daily intraday: skip"
                        )
                        return None
                    logger.info(
                        f"      🧠 IA confirma BET {side} | prob {ai_analysis.estimated_probability:.1%} "
                        f"| {ai_analysis.reasoning[:80]}"
                    )
            except Exception as e:
                logger.warning(f"      ⚠️ IA error: {e} — continuar con filtros base")

        # 5. Sizing — diferenciado por tipo de mercado.
        # Daily intraday "Up or Down on [today]" (5-May-2026): 0/6 LOST -$48,
        # mitad de stake mientras se valida el nuevo prompt + filtro anti-continuación.
        # Semanales "close above/below $X" (3W/0L incluyendo GOOGL +$6.14): full stake.
        if is_daily_intraday:
            bet_amount = min(
                STATE.current_bankroll * 0.06,  # 6% del bankroll
                SAFETY.max_bet_absolute * 0.7,  # ~$4.20 con max 6.0
                STATE.current_bankroll * 0.08,
            )
            bet_amount = max(bet_amount, 2.5)
        else:
            bet_amount = min(
                STATE.current_bankroll * 0.12,
                SAFETY.max_bet_absolute * 1.5,
                STATE.current_bankroll * 0.15,
            )
            bet_amount = max(bet_amount, 4.0)

        # Conviction sizing (2-Jun): full stake en tickers probados, 0.7x en los
        # marginales/sin data. Concentra el mismo presupuesto de riesgo en lo que
        # gana. NUNCA sube por encima del base — solo recorta los flojos.
        if index_key not in PROVEN_WINNERS:
            bet_amount = bet_amount * 0.7
            logger.info(
                f"      🎯 Conviction: {INDICES[index_key]['name']} marginal "
                f"(WR no probado) → stake 0.7x"
            )

        # Sizing inverso: edges >25% son anti-señal. Reducir stake a la
        # mitad para limitar daño cuando el modelo se equivoca, sin perder
        # las ocasiones reales que sí ganan dentro de ese bucket.
        if edge > HIGH_EDGE_SIZING_THRESHOLD:
            bet_amount = bet_amount * 0.5
            bet_amount = max(bet_amount, 2.0)
            logger.info(
                f"      🛡️ Edge alto {edge:.1%}>25%: stake reducido a la "
                f"mitad (anti-señal histórica)"
            )

        # Extreme-side bet (15-May): apostar la side cara con prob ≥80%.
        # Upside chico ($0.05 por $0.95 invertido = 5%), así que stake
        # absoluto limitado a $3 para mantener riesgo contenido.
        if _extreme_side_bet:
            bet_amount = min(bet_amount * 0.4, 3.0)
            bet_amount = max(bet_amount, 2.0)
            logger.info(
                f"      ⚡ Extreme-side sizing: ${bet_amount:.2f} "
                f"(stake reducido por payout chico)"
            )

        bet_amount = round(bet_amount, 2)

        # Clasificar subtipo para tracking de WR por formato.
        # Histórico muestra que "close above $X" y semanales rinden mejor
        # que daily intraday Up/Down genérico.
        q_lower_subtype = question.lower()
        if "up or down on" in q_lower_subtype:
            market_subtype = "daily_intraday"
        elif "finish week" in q_lower_subtype or "finish above" in q_lower_subtype or "finish below" in q_lower_subtype:
            market_subtype = "weekly"
        elif "close above" in q_lower_subtype or "close below" in q_lower_subtype:
            market_subtype = "close_target"
        else:
            market_subtype = "other"

        # 6. Ejecutar
        trade = {
            "strategy": "STOCKS",
            "market_subtype": market_subtype,
            "timestamp": datetime.now().isoformat(),
            "market_id": market_id,
            "question": question,
            "side": side,
            "amount": bet_amount,
            "price": price,
            "edge": edge,
            "prob": prob_direction if side == "YES" else 1 - prob_direction,
            "index": index_key,
            "direction": direction,
            "market_change": change,
            "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE"
        }

        if SAFETY.dry_run:
            trade["status"] = "SIMULATED"
            logger.info(f"      🏃 [DRY RUN] {side} ${bet_amount:.2f} @ {price:.2f}")
        else:
            logger.info(f"      💰 [LIVE] {side} ${bet_amount:.2f} @ {price:.2f}")
            try:
                executed = await self._execute_real_order(token_id, price, bet_amount)
                if executed:
                    trade["status"] = "EXECUTED"
                    STATE.current_bankroll -= bet_amount
                    self.traded_markets.add(market_id)
                    self._save_bet(market_id, question)
                    # Registrar la dirección efectiva para bloquear la
                    # opuesta en próximos ciclos del mismo día.
                    self._register_bet_direction(index_key, effective_direction)
                    self._daily_stock_count["count"] += 1
                    STATE.total_trades += 1
                    STATE.open_positions += 1
                    logger.info(f"      ✅ Stock trade ejecutado! Capital: ${STATE.current_bankroll:.2f}")
                else:
                    trade["status"] = "FAILED"
                    self.traded_markets.add(market_id)
            except Exception as e:
                trade["status"] = "ERROR"
                trade["error"] = str(e)

        return trade

    # ═══════════════════════════════════════════════════════════════
    # PARSEAR PREGUNTA
    # ═══════════════════════════════════════════════════════════════

    # ETFs que tienen precio distinto al índice subyacente. Cuando el mercado
    # de Polymarket usa estos tickers, hay que fetch el ETF (no el índice) o
    # Yahoo devuelve el valor del índice (~10x el del ETF) y Sonnet alucina
    # gaps imposibles. 11-May SPY $740: el bot leyó S&P=$7,415 y declaró
    # "900% above target → BET 97%". Fix 13-May.
    _ETF_TICKERS = {"spy": "SPY", "qqq": "QQQ", "dia": "DIA", "iwm": "IWM"}

    def _parse_stock_question(self, question: str) -> Optional[Dict]:
        q = question.lower()
        result = {"index": None, "direction": "up", "threshold_pct": None,
                  "etf_symbol": None}

        # Word boundary matching para evitar 'dow' matcheando 'down'
        best_len = 0
        for key, info in INDICES.items():
            for alias in info["aliases"]:
                pattern = r'(?:^|[\s,;:\-\(\)])' + re.escape(alias) + r'(?:$|[\s,;:\-\(\)\'\"?!.])'
                if re.search(pattern, q):
                    if len(alias) > best_len:
                        result["index"] = key
                        best_len = len(alias)
        if not result["index"]:
            return None
        # ETF detect: si en la pregunta aparece SPY/QQQ/DIA/IWM como token
        # explícito, usar ese ticker (no el índice subyacente). Detectado
        # independiente del longest-alias winner — "S&P 500 (SPY)" gana
        # "s&p 500" en longitud pero el target ($740) es del ETF SPY.
        for etf_alias, etf_ticker in self._ETF_TICKERS.items():
            pattern = r'(?:^|[\s,;:\-\(\)])' + re.escape(etf_alias) + r'(?:$|[\s,;:\-\(\)\'\"?!.])'
            if re.search(pattern, q):
                result["etf_symbol"] = etf_ticker
                break

        if any(w in q for w in ["close down", "close lower", "close red", "drop", "fall", "decline"]):
            result["direction"] = "down"
        else:
            result["direction"] = "up"

        pct = re.search(r'(\d+\.?\d*)%', q)
        if pct:
            result["threshold_pct"] = float(pct.group(1))

        return result

    # ═══════════════════════════════════════════════════════════════
    # DATOS DE MERCADO (Yahoo Finance)
    # ═══════════════════════════════════════════════════════════════

    async def _get_market_data(self, index_key: str,
                                override_symbol: Optional[str] = None) -> Optional[Dict]:
        """Obtiene datos de Yahoo Finance.

        override_symbol: si se provee, se usa en lugar del symbol del INDICES.
        Necesario para mercados que usan tickers de ETF (SPY/QQQ/DIA/IWM) cuyo
        precio es ~10x menor que el índice subyacente (^GSPC/^IXIC/^DJI/^RUT).
        """
        symbol = override_symbol or INDICES[index_key]["symbol"]
        cache_key = f"stock:{symbol}"
        if cache_key in self.cache:
            c = self.cache[cache_key]
            if time.time() - c["ts"] < self.cache_ttl:
                return c["data"]

        session = await self._get_session()

        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            headers = {"User-Agent": "Mozilla/5.0"}
            params = {"range": "5d", "interval": "1d", "includePrePost": "true"}

            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            result_list = data.get("chart", {}).get("result", [])
            if not result_list:
                return None

            chart = result_list[0]
            meta = chart.get("meta", {})
            indicators = chart.get("indicators", {}).get("quote", [{}])[0]

            price = meta.get("regularMarketPrice", 0)
            prev_close = meta.get("chartPreviousClose") or meta.get("previousClose", 0)
            change_pct = (price - prev_close) / prev_close if prev_close else 0

            # Historial para distribución
            closes = indicators.get("close", [])
            daily_returns = []
            for i in range(1, len(closes)):
                if closes[i] and closes[i-1] and closes[i-1] > 0:
                    daily_returns.append((closes[i] - closes[i-1]) / closes[i-1])

            state = meta.get("marketState", "REGULAR")

            result = {
                "price": price,
                "prev_close": prev_close,
                "change_pct": change_pct,
                "daily_returns": daily_returns,
                "state": state,
                "pre_market": meta.get("preMarketPrice"),
            }

            # Futuros si mercado cerrado (solo si no es override de ETF —
            # los ETFs usan su propio ticker spot, no necesitan futuros)
            if state in ("PRE", "POST", "CLOSED") and not override_symbol:
                fut = await self._get_futures(INDICES[index_key]["futures"])
                if fut:
                    result["futures"] = fut

            self.cache[cache_key] = {"data": result, "ts": time.time()}
            return result

        except Exception as e:
            logger.error(f"   📈 Yahoo Finance error: {e}")
            return None

    async def _get_futures(self, symbol: str) -> Optional[Dict]:
        session = await self._get_session()
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            headers = {"User-Agent": "Mozilla/5.0"}
            async with session.get(url, params={"range": "1d", "interval": "5m"},
                                   headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                p = meta.get("regularMarketPrice", 0)
                pc = meta.get("previousClose", 1)
                return {"price": p, "change_pct": (p - pc) / pc if pc else 0}
        except Exception:
            return None

    async def _get_vix(self) -> Optional[float]:
        """Obtiene el VIX actual. Retorna None si falla.

        Yahoo rate-limits (HTTP 429) cuando falta User-Agent.
        Fallback a Stooq CSV si Yahoo está caído.
        """
        session = await self._get_session()
        headers = {"User-Agent": "Mozilla/5.0"}

        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("chart", {}).get("result", [])
                    if result:
                        price = result[0].get("meta", {}).get("regularMarketPrice")
                        if price:
                            return float(price)
                else:
                    logger.debug(f"VIX Yahoo status={resp.status}, probando Stooq")
        except Exception as e:
            logger.debug(f"VIX Yahoo error: {e}, probando Stooq")

        try:
            url = "https://stooq.com/q/l/?s=%5Evix&f=sd2t2ohlc&h&e=csv"
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text()
                lines = text.strip().split("\n")
                if len(lines) < 2:
                    return None
                cols = lines[1].split(",")
                if len(cols) < 7:
                    return None
                close = cols[6]
                if close in ("N/D", "", "0"):
                    return None
                return float(close)
        except Exception as e:
            logger.debug(f"VIX Stooq error: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════
    # CÁLCULO DE PROBABILIDAD
    # ═══════════════════════════════════════════════════════════════

    def _calculate_prob(self, data: Dict, direction: str,
                        threshold_pct: float = None) -> float:
        """
        P(direction) combinando:
        1. Momentum intraday
        2. Futuros pre-market
        3. Distribución histórica
        4. Hora del día (más confiable cerca del cierre)
        """
        change = data.get("change_pct", 0)
        returns = data.get("daily_returns", [])
        state = data.get("state", "REGULAR")
        futures = data.get("futures", {})

        # Base histórica
        if returns:
            if threshold_pct:
                t = threshold_pct / 100
                hist = sum(1 for r in returns if (r > t if direction == "up" else r < -t)) / len(returns)
            else:
                hist = sum(1 for r in returns if (r > 0 if direction == "up" else r < 0)) / len(returns)
        else:
            hist = 0.52 if direction == "up" else 0.48

        # Momentum
        momentum = 0.5
        if state == "REGULAR":
            if direction == "up":
                if change > 0.005:
                    momentum = min(0.70 + change * 5, 0.90)
                elif change > 0:
                    momentum = 0.55 + change * 10
                else:
                    momentum = max(0.15, 0.40 + change * 5)
            else:
                if change < -0.005:
                    momentum = min(0.70 + abs(change) * 5, 0.90)
                elif change < 0:
                    momentum = 0.55 + abs(change) * 10
                else:
                    momentum = max(0.15, 0.40 - change * 5)
        elif futures:
            fc = futures.get("change_pct", 0)
            if direction == "up":
                momentum = max(0.10, min(0.90, 0.50 + fc * 8))
            else:
                momentum = max(0.10, min(0.90, 0.50 - fc * 8))

        # Peso del momentum según hora
        now_utc = datetime.now(timezone.utc)
        et_hour = (now_utc.hour - 5) % 24  # Aprox ET

        if state == "REGULAR":
            if et_hour >= 15:
                w = 0.80
            elif et_hour >= 13:
                w = 0.65
            elif et_hour >= 11:
                w = 0.50
            else:
                w = 0.35
        else:
            w = 0.25

        prob = hist * (1 - w) + momentum * w
        return max(0.05, min(0.95, prob))

    # ═══════════════════════════════════════════════════════════════
    # EJECUCIÓN REAL
    # ═══════════════════════════════════════════════════════════════

    async def _execute_real_order(self, token_id: str, price: float,
                                   amount: float) -> bool:
        """Ejecuta orden real (mismo patrón que btc_15min)."""
        try:
            from py_clob_client_v2.client import ClobClient
            from py_clob_client_v2.clob_types import MarketOrderArgs, OrderArgs, OrderType
            from py_clob_client_v2 import Side
            BUY = Side.BUY

            pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if not pk:
                return False
            pk_clean = pk[2:] if pk.startswith("0x") else pk
            sig_type = int(os.getenv("SIGNATURE_TYPE", "2"))
            funder_param = os.getenv("POLYMARKET_FUNDER_ADDRESS") if sig_type > 0 else None

            client = ClobClient(
                host="https://clob.polymarket.com",
                key=pk_clean, chain_id=137,
                signature_type=sig_type, funder=funder_param
            )
            client.set_api_creds(client.create_or_derive_api_key())

            # FOK
            try:
                mo = MarketOrderArgs(token_id=token_id, amount=amount, side=BUY)
                signed = client.create_market_order(mo)
                resp = client.post_order(signed, OrderType.FOK)
                if resp and isinstance(resp, dict):
                    oid = resp.get("orderID", "")
                    if (resp.get("success") or resp.get("status") == "matched") and oid:
                        logger.info(f"      ✅ FOK ejecutada: {oid[:20]}...")
                        return True
            except Exception as e:
                logger.debug(f"      FOK falló: {str(e)[:60]}")

            # GTC
            try:
                limit_price = min(price + 0.03, 0.95)
                size = round(amount / max(price, 0.01), 2)
                lo = OrderArgs(token_id=token_id, price=round(limit_price, 2),
                               size=size, side=BUY)
                signed_l = client.create_order(lo)
                resp_l = client.post_order(signed_l, OrderType.GTC)
                if resp_l and isinstance(resp_l, dict):
                    oid = resp_l.get("orderID", "")
                    if oid or resp_l.get("success"):
                        logger.info(f"      ✅ GTC ejecutada: {oid[:20]}...")
                        return True
            except Exception as e:
                logger.debug(f"      GTC falló: {str(e)[:60]}")

            return False
        except Exception as e:
            logger.error(f"      Error CLOB: {e}")
            return False

    def get_stats(self) -> str:
        return f"📈 Stocks: tracking {', '.join(INDICES.keys())}"
