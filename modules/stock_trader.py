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

MIN_EDGE = 0.08  # 8% (data más confiable que weather)


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

    def _is_opposite_bet(self, index_key: str, direction: str) -> bool:
        """
        ¿Ya apostamos la dirección CONTRARIA de este ticker HOY?

        Protege contra el patrón observado el 14-Apr:
        bot apostó Google Up + Google Down el mismo día → Down perdió
        garantizado, Up perdió también por movimiento lateral.
        Apostar ambos lados en stocks correlacionados es matemática-
        mente -EV salvo en escenarios muy específicos.

        Nota: permitimos múltiples apuestas en la MISMA dirección
        (ej: Amazon Up + Amazon close above $245 ambos bull) porque
        ganan juntas. Solo bloqueamos direcciones opuestas.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if self._today_directions["date"] != today:
            return False
        existing = self._today_directions["data"].get(index_key, set())
        if not existing:
            return False
        opposite = "DOWN" if direction.upper() == "UP" else "UP"
        return opposite in existing

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

        # Filtrar server-side por endDate: los mercados stocks diarios
        # (Up/Down hoy, close above $X hoy) están enterrados después de 1000+
        # long-term si se paginar por volumen. Con end_date_min/max el API
        # devuelve directamente los de <48h.
        now = datetime.now(timezone.utc)
        end_date_min = now.isoformat()
        end_date_max = (now + timedelta(hours=48)).isoformat()

        markets = []
        for offset in [0, 100, 200, 300, 400]:
            try:
                async with session.get(
                    f"{GAMMA_API_URL}/markets",
                    params={
                        "active": "true", "closed": "false",
                        "limit": 100, "offset": str(offset),
                        "order": "volume", "ascending": "false",
                        "end_date_min": end_date_min,
                        "end_date_max": end_date_max,
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

                                # Solo mercados que resuelven en 48h
                                end_str = m.get("endDate", "")
                                if end_str:
                                    try:
                                        end_dt = datetime.fromisoformat(
                                            end_str.replace("Z", "+00:00"))
                                        hours = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                                        if hours < 0 or hours > 48:
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
            # Override: si el edge es excepcional (>25%), dejar pasar.
            # Edges >25% son raros (1-2/semana) y casi siempre ganan.
            # Ejemplo: AMZN >$245 con edge 72% → +$16.53
            # El override se evalúa DESPUÉS de calcular edge (más abajo),
            # así que aquí solo logeamos y seguimos — el check real
            # va después de calcular edge_yes/edge_no.
            logger.info(f"      ⚠️ Max 4 stock bets/día alcanzado — evaluando override por edge alto...")
            self._daily_limit_reached = True
        else:
            self._daily_limit_reached = False

        # Daily loss limit: si perdimos $15+ hoy, pausar stocks resto del
        # día. SPORTS sigue operando (baja varianza). Motivación: 21-Abr
        # stocks perdieron ~$38 consecutivos sin freno.
        if self._daily_loss_check["date"] != today:
            self._daily_loss_check = {
                "date": today,
                "start_balance": STATE.current_bankroll,
            }
        net_daily_pnl = STATE.current_bankroll - self._daily_loss_check["start_balance"]
        if net_daily_pnl <= -25:
            logger.warning(
                f"      ⛔ Daily loss limit NETO: P&L hoy ${net_daily_pnl:.2f} "
                f"(límite -$25). Stocks pausados hasta mañana."
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
                        f"Límite: -$25\n"
                        f"Stocks PAUSADOS hasta mañana.\n"
                        f"SPORTS sigue operando."
                    ))
            except Exception:
                pass
            return None
        elif net_daily_pnl < -15:
            logger.info(
                f"      ⚠️ P&L neto hoy: ${net_daily_pnl:.2f} "
                f"(acercándose al límite -$25)"
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

        q_lower = question.lower()
        # Solo permitir mercados "Up or Down". Los "close above/below"
        # tienen 40% WR vs 67% de Up/Down. Datos del 27-Abr.
        if any(kw in q_lower for kw in [
            "close above", "close below",
            "finish week", "finish above", "finish below",
            "end above", "end below",
            "closes above", "closes below"
        ]):
            logger.info(f"      ⛔ Solo Up/Down: skip '{question[:40]}'")
            return None

        # 1. Parsear pregunta
        parsed = self._parse_stock_question(question)
        if not parsed:
            return None

        index_key = parsed["index"]
        direction = parsed["direction"]

        logger.info(f"   📈 {question[:55]}")

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

        # News sentiment filter
        try:
            news = self.news.get_sentiment()
            logger.info(f"      📰 News: {news['sentiment']} (score {news['score']:+d})")
            # Si noticias muy bearish y apostamos UP → skip
            if news["score"] <= -3 and direction.upper() == "UP":
                logger.info(f"      📰 Noticias bearish ({news['score']:+d}), skip UP")
                return None
            # Si noticias muy bullish y apostamos DOWN → skip
            if news["score"] >= 3 and direction.upper() == "DOWN":
                logger.info(f"      📰 Noticias bullish ({news['score']:+d}), skip DOWN")
                return None
        except Exception as e:
            logger.debug(f"      News check error: {e}")

        # Filtro de tendencia: si el S&P está bajando fuerte hoy,
        # NO apostar "Up" en ningún stock (correlación de mercado).
        # 20-Abr: mercado -2%, 5 bets Up perdieron -$34.
        # 21-Abr: 4 stocks perdieron sin log del filtro (silent fail en debug).
        # Fail-safe: si no se puede obtener data, skip (no apostar ciego).
        # NOTA (22-Abr): _parse_stock_question devuelve "up"/"down" lowercase,
        # así que direction.upper() cubre tanto "Up/Down" como "close above/below"
        # (above → parser default "up", below matchea "close lower"/"decline"
        # → "down"). Parser gap conocido: "closes below $X" sin verbos de caída
        # cae en default "up"; seguimiento en TODO separado.
        try:
            sp500_data = await self._get_market_data("sp500")
            if sp500_data is None:
                logger.warning(f"      ⚠️ No se pudo obtener S&P data — skip por precaución")
                return None
            market_change = sp500_data.get("change_pct", 0)
            logger.info(f"      📊 S&P tendencia: {market_change:+.2%}")
            # Umbral bajado de ±1% a ±0.5% tras pérdida 4/4 del 21-Abr.
            if market_change < -0.005 and direction.upper() == "UP":
                logger.info(
                    f"      📉 Mercado bajando ({market_change:+.2%}), "
                    f"skip bet UP en {INDICES[index_key]['name']}"
                )
                return None
            if market_change > 0.005 and direction.upper() == "DOWN":
                logger.info(
                    f"      📈 Mercado subiendo ({market_change:+.2%}), "
                    f"skip bet DOWN en {INDICES[index_key]['name']}"
                )
                return None
        except Exception as e:
            logger.warning(f"      ⚠️ Error trend check: {e} — skip por precaución")
            return None

        # 2. Obtener datos del mercado
        mkt_data = await self._get_market_data(index_key)
        if not mkt_data:
            logger.info(f"      No se pudo obtener datos de {INDICES[index_key]['name']}")
            return None

        # Gap filter (17-Abr): "close above $X" / "close below $X" con target
        # lejano pierden 3/3 esta semana (AAPL >$255, AMZN >$250, NVDA >$200).
        # Up/Down simples mantienen 90% WR.
        # Ext 23-Abr: mercados semanales ("finish week above $X") también —
        # META $690 pasó con gap real 3.45% (-$9.22). Umbral 5% para semanal.
        import re as _re
        q_lower = question.lower()
        target_match = _re.search(r'\$(\d+(?:,\d{3})*(?:\.\d+)?)', question)
        daily_kw = ("close above" in q_lower or "close below" in q_lower)
        weekly_kw = any(kw in q_lower for kw in (
            "finish week", "finish above", "finish below",
            "end above", "end below",
        ))
        if target_match and (daily_kw or weekly_kw):
            try:
                target_price = float(target_match.group(1).replace(",", ""))
                current_price = mkt_data.get("price", 0)
                if current_price > 0:
                    gap_pct = abs(target_price - current_price) / current_price
                    is_weekly = weekly_kw and not daily_kw
                    max_gap = 0.03
                    if gap_pct > max_gap:
                        kind = "semanal" if is_weekly else "diario"
                        logger.info(
                            f"      Gap {gap_pct:.1%} > {max_gap:.0%} para "
                            f"{kind} target ${target_price:.0f} vs "
                            f"actual ${current_price:.0f}: skip"
                        )
                        return None
            except (ValueError, AttributeError):
                pass

        change = mkt_data.get("change_pct", 0)
        logger.info(f"      {INDICES[index_key]['name']}: ${mkt_data['price']:,.0f} | "
                     f"Cambio: {change:+.2%} | Estado: {mkt_data.get('state', '?')}")

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

        # VALIDACIÓN: rechazar precios inválidos
        if yes_price < 0.02 or yes_price > 0.98 or no_price < 0.02 or no_price > 0.98:
            logger.info(f"      Precios fuera de rango (YES={yes_price:.2f}, NO={no_price:.2f}), skip")
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

        if edge_yes > edge_no and edge_yes >= MIN_EDGE:
            side, edge, price, token_id = "YES", edge_yes, yes_price, tokens[0]
        elif edge_no >= MIN_EDGE:
            side, edge, price, token_id = "NO", edge_no, no_price, tokens[1]
        else:
            logger.info(f"      Edge YES={edge_yes:+.1%}, NO={edge_no:+.1%} → insuficiente")
            return None

        # Override check: si ya llegamos al límite diario, solo permitir
        # si el edge es excepcional
        if getattr(self, '_daily_limit_reached', False) and edge < 0.25:
            logger.info(f"      ⛔ Max 5/día + edge {edge:.1%} < 25%: skip")
            return None
        elif getattr(self, '_daily_limit_reached', False) and edge >= 0.25:
            logger.info(f"      🔥 OVERRIDE: edge {edge:.1%} >= 25% — apuesta a pesar del límite diario")

        # Colas largas (precio < 10¢ o > 90¢) tienen alta varianza y
        # poco upside realista. El único STOCKS LOST histórico fue
        # SPX Up/Down @ $0.060 → −$7.50 (14-Abr). Más estricto que
        # el filtro 0.02/0.98 que ya existe arriba.
        if price < 0.10 or price > 0.90:
            logger.info(f"      Cola larga @ {price:.3f}: skip")
            return None

        # Determinar la dirección EFECTIVA que estamos apostando:
        # - Mercado "Up or Down" con YES → bet UP (direction ya es UP)
        # - Mercado "Up or Down" con NO → bet DOWN (opuesto a direction)
        # - Mercado "close above $X" con YES → bet UP
        # - Mercado "close above $X" con NO → bet DOWN
        effective_direction = direction.upper()
        if side == "NO":
            effective_direction = "DOWN" if effective_direction == "UP" else "UP"

        # Bloquear si ya apostamos en dirección OPUESTA hoy.
        # Evita casos como Google Up + Google Down el mismo día
        # (pérdida garantizada en uno de los dos).
        if self._is_opposite_bet(index_key, effective_direction):
            existing = self._today_directions["data"].get(index_key, set())
            logger.info(
                f"      ⛔ Skip: {INDICES[index_key]['name']} ya "
                f"apostado HOY en {existing} (correlación negativa)"
            )
            return None

        logger.info(f"      🎯 EDGE {side}: {edge:.1%}")

        # 5. Sizing — ESTRATEGIA PRINCIPAL, apuesta más grande
        # Stocks: 3W/0L (100% WR, +$19.82) — nuestra mejor estrategia
        bet_amount = min(
            STATE.current_bankroll * 0.12,      # 12% del bankroll (era 8%)
            SAFETY.max_bet_absolute * 1.5,      # 50% extra vs normal
            STATE.current_bankroll * 0.15        # Techo 15%
        )
        bet_amount = max(bet_amount, 4.0)
        bet_amount = round(bet_amount, 2)

        # 6. Ejecutar
        trade = {
            "strategy": "STOCKS",
            "timestamp": datetime.now().isoformat(),
            "market_id": market_id,
            "question": question,
            "side": side,
            "amount": bet_amount,
            "price": price,
            "edge": edge,
            "probability": prob_direction if side == "YES" else 1 - prob_direction,
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

    def _parse_stock_question(self, question: str) -> Optional[Dict]:
        q = question.lower()
        result = {"index": None, "direction": "up", "threshold_pct": None}

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

    async def _get_market_data(self, index_key: str) -> Optional[Dict]:
        """Obtiene datos de Yahoo Finance."""
        cache_key = f"stock:{index_key}"
        if cache_key in self.cache:
            c = self.cache[cache_key]
            if time.time() - c["ts"] < self.cache_ttl:
                return c["data"]

        idx = INDICES[index_key]
        session = await self._get_session()

        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{idx['symbol']}"
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

            # Futuros si mercado cerrado
            if state in ("PRE", "POST", "CLOSED"):
                fut = await self._get_futures(idx["futures"])
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
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
            if not pk:
                return False
            pk_clean = pk[2:] if pk.startswith("0x") else pk

            client = ClobClient(
                host="https://clob.polymarket.com",
                key=pk_clean, chain_id=137, signature_type=0
            )
            client.set_api_creds(client.create_or_derive_api_creds())

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
