"""
PolyBot - Strategy 7: Football (Soccer) Trader
================================================
Tradea mercados de fútbol en Polymarket usando estadísticas reales.

Fuentes gratuitas sin API key:
  - ClubElo.com: ratings históricos de equipos (Elo)
  - ESPN API no-oficial: partidos y forma reciente
  - Polymarket Gamma API: mercados activos

Ventaja competitiva:
  - Detecta cuando el mercado sobrevalora al favorito
  - Encuentra underdogs con valor real (Elo gap pequeño pero price gap grande)
  - Edge mínimo 12% (más alto que otras estrategias por varianza del fútbol)
"""

import re
import json
import time
import logging
import aiohttp
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timezone, timedelta
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.football")

GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Edge mínimo — bajado 20-Jun para Mundial 2026 (thresholds altos impedían apostar)
MIN_EDGE_FAVORITE  = 0.06   # 6% para apostar al favorito
MIN_EDGE_UNDERDOG  = 0.08   # 8% para apostar al underdog (mayor varianza)
MIN_EDGE_DRAW      = 0.07   # 7% para draws

# Ventaja de local en Elo (estándar FIFA/ClubElo: ~+65 puntos)
HOME_ELO_ADVANTAGE = 65

# Torneos internacionales/neutral venue → sin ventaja de local
NEUTRAL_VENUE_KW = [
    "world cup", "copa america", "euro", "nations league", "champions league final",
    "europa league final", "club world cup", "olympic", "olimpic"
]

# Ligas prioritarias (mejor liquidez y estadísticas)
PRIORITY_LEAGUES = [
    # Mundial — máxima prioridad
    "world cup", "fifa world cup", "2026 world cup", "world cup 2026",
    "group stage", "group a", "group b", "group c", "group d", "group e",
    "group f", "group g", "group h", "round of 16", "quarterfinal", "semifinal",
    # Copas continentales
    "copa america", "euro 2024", "nations league", "concacaf",
    # Clubes
    "champions league", "uefa", "europa league", "premier league", "la liga",
    "serie a", "bundesliga", "ligue 1", "copa libertadores",
    "mls", "eredivisie", "brasileirao", "fa cup", "conmebol",
]

FOOTBALL_MATCH_KW = [
    " vs ", " v ", " vs. ", "beat ", "beats ", "win ", "wins ", "draw ",
    "match ", "game ", "score ", "goal ", "result ", "advance", "qualify",
    "final", "semi-final", "quarterfinal", "knockout", "eliminate", "progress",
    "advance to", "through to", "group stage",
    "over ", "under ", "goals ", "total goals",
]

# Ratings Elo de SELECCIONES nacionales. ClubElo.com solo cubre CLUBES — para
# el Mundial (selecciones) devuelve None y el pricing caía a precio de mercado
# (edge 0 → nunca apostaba). eloratings.net (World Football Elo) sí cubre
# selecciones y expone un TSV con ratings en vivo: col 2 = código país, col 3 = Elo.
NATIONAL_ELO_URL = "https://www.eloratings.net/World.tsv"

# Mapeo nombre (normalizado, sin acentos) → código eloratings.net.
# Cubre las selecciones del Mundial 2026 + variantes que usa Polymarket
# ("IR Iran", "DR Congo", "Korea Republic", "Cote d'Ivoire", etc.).
NATIONAL_TEAM_CODES = {
    "usa": "US", "united states": "US", "united states of america": "US",
    "mexico": "MX", "canada": "CA",
    "argentina": "AR", "brazil": "BR", "uruguay": "UY", "colombia": "CO",
    "ecuador": "EC", "paraguay": "PY", "peru": "PE", "chile": "CL",
    "venezuela": "VE", "bolivia": "BO",
    "spain": "ES", "france": "FR", "england": "EN", "portugal": "PT",
    "netherlands": "NL", "germany": "DE", "belgium": "BE", "croatia": "HR",
    "italy": "IT", "switzerland": "CH", "denmark": "DK", "norway": "NO",
    "sweden": "SE", "austria": "AT", "turkey": "TR", "turkiye": "TR",
    "ukraine": "UA", "poland": "PL", "serbia": "RS", "greece": "GR",
    "czechia": "CZ", "czech republic": "CZ", "hungary": "HU", "slovakia": "SK",
    "slovenia": "SI", "romania": "RO", "ireland": "IE", "wales": "WA",
    "japan": "JP", "south korea": "KR", "korea republic": "KR", "korea": "KR",
    "iran": "IR", "ir iran": "IR", "australia": "AU", "saudi arabia": "SA",
    "qatar": "QA", "jordan": "JO", "uzbekistan": "UZ", "new zealand": "NZ",
    "morocco": "MA", "senegal": "SN", "nigeria": "NG", "egypt": "EG",
    "cote d ivoire": "CI", "cote divoire": "CI", "ivory coast": "CI",
    "ghana": "GH", "cameroon": "CM",
    "algeria": "DZ", "tunisia": "TN", "dr congo": "CD", "congo dr": "CD",
    "south africa": "ZA", "angola": "AO", "mozambique": "MZ",
    "curacao": "CW", "panama": "PA", "costa rica": "CR", "honduras": "HN",
    "el salvador": "SV", "trinidad and tobago": "TT", "suriname": "SR",
    "new caledonia": "NC",
}

# Keywords que NO son fútbol (excluir)
NOT_FOOTBALL_KW = [
    "nfl", "nba", "mlb", "nhl", "ufc", "mma", "tennis", "golf", "f1",
    "formula 1", "basketball", "baseball", "hockey", "rugby", "cricket",
    "boxing", "wrestling", "olympics", "super bowl", "march madness"
]


class FootballTrader:
    """Estrategia de trading en mercados de fútbol de Polymarket."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache: Dict = {}
        self.cache_ttl = 300          # 5 min de caché para datos de fútbol
        self.last_run = 0
        self.min_interval = 180       # Mínimo 3 min entre escaneos
        self.traded_markets: set = set()
        self.elo_cache: Dict = {}     # Caché de ratings Elo por equipo
        self._national_elo: Optional[Dict[str, float]] = None  # {código: Elo}
        self._national_elo_ts = 0.0
        self._load_traded()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
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
            import os
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
                    "strategy": "FOOTBALL"
                })
            with open("data/bets_placed.json", "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # PUNTO DE ENTRADA
    # ═══════════════════════════════════════════════════════════════

    async def run_cycle(self) -> Optional[Dict]:
        """Busca y tradea mercados de fútbol con edge estadístico."""
        if STATE.is_paused:
            return None

        now = time.time()
        if now - self.last_run < self.min_interval:
            return None
        self.last_run = now

        logger.info("⚽ Football Trader: Buscando mercados de fútbol...")

        markets = await self._find_football_markets()
        if not markets:
            logger.info("   ⚽ No se encontraron mercados de fútbol activos")
            return None

        logger.info(f"   ⚽ {len(markets)} mercados de fútbol encontrados")

        for market in markets[:30]:
            try:
                trade = await self._analyze_and_trade(market)
                if trade:
                    return trade
            except Exception as e:
                logger.debug(f"   ⚽ Error analizando mercado: {e}")

        logger.info("   ⚽ Sin oportunidades de fútbol en este ciclo")
        return None

    # ═══════════════════════════════════════════════════════════════
    # BUSCAR MERCADOS DE FÚTBOL
    # ═══════════════════════════════════════════════════════════════

    # Tags de Polymarket que agrupan partidos de fútbol/Mundial.
    # IMPORTANTE: los partidos individuales (moneyline "Will X win?") viven en
    # el endpoint /events, NO en /markets, y el kickoff real está en el campo
    # `gameStartTime` de cada sub-mercado (NO en startDate, que es la fecha de
    # creación del mercado). El parámetro `tag` de /markets es ignorado por
    # Gamma — por eso la versión anterior nunca encontraba partidos.
    EVENT_TAGS = ["world-cup", "fifa-world-cup", "soccer"]
    KICKOFF_MIN_H = 1.0    # no apostar a <1h del inicio (libro volátil)
    KICKOFF_MAX_H = 48.0   # partidos se publican ~2 días antes

    def _kickoff_dt(self, event: Dict) -> Optional[datetime]:
        """Kickoff real del partido desde gameStartTime del primer sub-mercado."""
        for m in (event.get("markets") or []):
            g = m.get("gameStartTime")
            if g:
                try:
                    return datetime.fromisoformat(
                        str(g).replace(" ", "T").replace("+00", "+00:00"))
                except Exception:
                    pass
        ed = event.get("endDate")
        if ed:
            try:
                return datetime.fromisoformat(ed.replace("Z", "+00:00"))
            except Exception:
                pass
        return None

    @staticmethod
    def _split_event_teams(title: str) -> Tuple[Optional[str], Optional[str]]:
        """'Helsingborgs IF vs. Landskrona BoIS' → ('Helsingborgs IF', 'Landskrona BoIS')."""
        t = re.split(r'\s+vs\.?\s+|\s+v\s+', title, maxsplit=1, flags=re.IGNORECASE)
        if len(t) == 2:
            # Quitar sufijos tipo "- Total Corners" del segundo equipo
            b = re.split(r'\s+[-–]\s+', t[1])[0].strip()
            return t[0].strip(), b
        return None, None

    async def _find_football_markets(self) -> List[Dict]:
        """Busca partidos de fútbol (moneyline) en Polymarket vía /events.

        Estructura real (jun-2026): cada partido es un EVENTO 'A vs. B' con
        sub-mercados binarios. El moneyline es `sportsMarketType == "moneyline"`
        con pregunta 'Will <equipo> win on <fecha>?' y outcomes ["Yes","No"].
        """
        session = await self._get_session()
        cache_key = "football_markets"
        if cache_key in self.cache:
            c = self.cache[cache_key]
            if time.time() - c["ts"] < self.cache_ttl:
                return c["data"]

        now = datetime.now(timezone.utc)
        events: Dict[str, Dict] = {}
        for tag in self.EVENT_TAGS:
            for offset in [0, 60, 120, 180, 240, 300]:
                try:
                    async with session.get(
                        f"{GAMMA_API_URL}/events",
                        params={
                            "active": "true", "closed": "false",
                            "limit": 60, "offset": str(offset),
                            "tag_slug": tag,
                            "order": "startDate", "ascending": "false",
                        }
                    ) as resp:
                        if resp.status != 200:
                            break
                        batch = await resp.json()
                        if not batch:
                            break
                        for e in batch:
                            events[str(e.get("id"))] = e
                except Exception:
                    break

        filtered: List[Dict] = []
        for e in events.values():
            title = e.get("title", "") or ""
            tlow = title.lower()
            if any(kw in tlow for kw in NOT_FOOTBALL_KW):
                continue

            kickoff = self._kickoff_dt(e)
            if not kickoff:
                continue
            hours = (kickoff - now).total_seconds() / 3600
            if not (self.KICKOFF_MIN_H <= hours <= self.KICKOFF_MAX_H):
                continue

            team_a, team_b = self._split_event_teams(title)
            if not team_a or not team_b:
                continue

            # Mundial / internacional → venue neutral (sin ventaja de local)
            etags = " ".join(
                (t.get("slug", "") if isinstance(t, dict) else str(t))
                for t in (e.get("tags") or [])
            ).lower()
            neutral = ("world-cup" in etags or "fifa-world-cup" in etags
                       or any(kw in tlow for kw in NEUTRAL_VENUE_KW))

            for m in (e.get("markets") or []):
                if (m.get("sportsMarketType") or "") != "moneyline":
                    continue
                mid = str(m.get("id", ""))
                cid = m.get("conditionId", "")
                if mid in self.traded_markets or cid in self.traded_markets:
                    continue
                if not m.get("acceptingOrders", True) or m.get("closed"):
                    continue
                liq = float(m.get("liquidity", 0) or m.get("liquidityNum", 0) or 0)
                if liq < 1000:
                    continue

                q = (m.get("question") or "")
                ql = q.lower()
                is_draw = "draw" in ql or "tie" in ql
                if is_draw:
                    subject = None   # empate: ningún equipo es el sujeto
                else:
                    # Identificar a qué equipo se refiere el "Yes".
                    # El título completo (con ambos equipos) NO está en la
                    # pregunta de victoria — solo el nombre del equipo sujeto.
                    subject = team_a if team_a.lower() in ql else (
                        team_b if team_b.lower() in ql else None)
                    if subject is None:
                        continue

                m = dict(m)  # copia para enriquecer sin tocar el caché de eventos
                m["_event_title"] = title
                m["_team_a"] = team_a
                m["_team_b"] = team_b
                m["_subject"] = subject          # equipo del "Yes" (None si draw)
                m["_is_draw"] = is_draw
                m["_home_team"] = None if neutral else team_a
                m["_neutral"] = neutral
                m["_kickoff_h"] = round(hours, 1)
                filtered.append(m)

        filtered.sort(key=lambda m: float(m.get("liquidity", 0) or 0), reverse=True)
        logger.info(f"   ⚽ {len(filtered)} mercados moneyline en ventana "
                    f"{self.KICKOFF_MIN_H:.0f}-{self.KICKOFF_MAX_H:.0f}h")
        self.cache[cache_key] = {"data": filtered, "ts": time.time()}
        return filtered

    def _is_football_market(self, market: Dict) -> bool:
        """Determina si un mercado es de fútbol/soccer."""
        q = (market.get("question") or "").lower()
        tags = [t.lower() for t in (market.get("tags") or [])]
        category = (market.get("category") or "").lower()

        if any(kw in q for kw in NOT_FOOTBALL_KW):
            return False
        if "soccer" in tags or "football" in tags:
            return True
        if "soccer" in category or "football" in category:
            return True
        if any(lg in q for lg in PRIORITY_LEAGUES):
            if any(kw in q for kw in FOOTBALL_MATCH_KW):
                return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # ANALIZAR MERCADO
    # ═══════════════════════════════════════════════════════════════

    async def _analyze_and_trade(self, market: Dict) -> Optional[Dict]:
        """Analiza un mercado de fútbol y ejecuta si hay edge."""
        question = market.get("question", "")
        market_id = str(market.get("id", ""))

        outcomes = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str):
            try:
                prices = json.loads(outcomes)
            except Exception:
                return None
        else:
            prices = outcomes

        if len(prices) < 2:
            return None

        yes_price = float(prices[0])
        no_price = float(prices[1])

        # Rechazar precios extremos (ya muy seguro = poca ganancia)
        if yes_price > 0.95 or yes_price < 0.05:
            return None
        if no_price > 0.95 or no_price < 0.05:
            return None

        tokens = market.get("clobTokenIds", "[]")
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except Exception:
                return None
        if len(tokens) < 2:
            return None

        logger.info(f"   ⚽ Analizando: {question[:60]}")

        # Parsear equipos (usa el contexto del evento si está enriquecido)
        parsed = self._parse_match_question(question, market)

        # Calcular probabilidad estadística
        true_prob_yes = await self._estimate_true_probability(
            question, parsed, yes_price
        )

        if true_prob_yes is None:
            logger.info(f"      Sin datos estadísticos suficientes")
            return None

        logger.info(f"      Mercado YES={yes_price:.2f} | Est. P(YES)={true_prob_yes:.2f}")

        edge_yes = true_prob_yes - yes_price
        edge_no = (1.0 - true_prob_yes) - no_price

        # Determinar si hay edge suficiente
        bet_type = self._classify_bet(yes_price, true_prob_yes)
        min_edge = self._get_min_edge(bet_type)

        if edge_yes >= min_edge and edge_yes > edge_no:
            side, edge, price, token_id = "YES", edge_yes, yes_price, tokens[0]
            win_prob = true_prob_yes
        elif edge_no >= min_edge:
            side, edge, price, token_id = "NO", edge_no, no_price, tokens[1]
            win_prob = 1.0 - true_prob_yes
        else:
            logger.info(f"      Edge YES={edge_yes:+.1%}, NO={edge_no:+.1%} → insuficiente (mín {min_edge:.0%})")
            return None

        logger.info(f"      🎯 {bet_type.upper()} | Edge {side}: {edge:.1%} | P(win)={win_prob:.1%}")

        # Sizing conservador para fútbol (más varianza)
        kelly_size = edge / (1.0 / win_prob - 1.0) if win_prob < 1.0 else 0
        kelly_bet = STATE.current_bankroll * kelly_size * SAFETY.kelly_fraction
        bet_amount = max(SAFETY.min_bet_size, min(kelly_bet, SAFETY.max_bet_absolute))
        # Extra conservador para underdogs
        if bet_type == "underdog":
            bet_amount = min(bet_amount, SAFETY.max_bet_absolute * 0.7)
        bet_amount = round(bet_amount, 2)

        trade = {
            "strategy": "FOOTBALL",
            "timestamp": datetime.now().isoformat(),
            "market_id": market_id,
            "question": question,
            "side": side,
            "amount": bet_amount,
            "price": price,
            "edge": edge,
            "probability": win_prob,
            "bet_type": bet_type,
            "mode": "DRY_RUN" if SAFETY.dry_run else "LIVE"
        }

        if SAFETY.dry_run:
            trade["status"] = "SIMULATED"
            logger.info(f"      🏃 [DRY RUN] {side} ${bet_amount:.2f} @ {price:.2f} ({bet_type})")
        else:
            logger.info(f"      💰 [LIVE] {side} ${bet_amount:.2f} @ {price:.2f} ({bet_type})")
            try:
                import os
                # CLOB v2 (mismo patrón que stock_trader._execute_real_order).
                # La API v1 daba order_version_mismatch tras la migración 28-Abr.
                from py_clob_client_v2.client import ClobClient
                from py_clob_client_v2.clob_types import MarketOrderArgs, OrderArgs, OrderType
                from py_clob_client_v2 import Side
                BUY = Side.BUY

                pk = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
                if not pk:
                    trade["status"] = "NO_KEY"
                    return trade
                pk_clean = pk[2:] if pk.startswith("0x") else pk
                sig_type = int(os.getenv("SIGNATURE_TYPE", "2"))
                funder_param = os.getenv("POLYMARKET_FUNDER_ADDRESS") if sig_type > 0 else None

                client = ClobClient(
                    host="https://clob.polymarket.com",
                    key=pk_clean, chain_id=137,
                    signature_type=sig_type, funder=funder_param
                )
                client.set_api_creds(client.create_or_derive_api_key())

                executed = False
                try:
                    mo = MarketOrderArgs(token_id=token_id, amount=bet_amount, side=BUY)
                    signed = client.create_market_order(mo)
                    resp = client.post_order(signed, OrderType.FOK)
                    if resp and (resp.get("success") or resp.get("status") == "matched"):
                        executed = True
                        logger.info(f"      ✅ FOK ejecutada: {resp.get('orderID','')[:20]}...")
                except Exception as e:
                    logger.debug(f"      FOK falló: {str(e)[:60]}")

                if not executed:
                    try:
                        limit_price = min(price + 0.02, 0.96)
                        size = round(bet_amount / max(price, 0.01), 2)
                        lo = OrderArgs(token_id=token_id,
                                       price=round(limit_price, 2),
                                       size=size, side=BUY)
                        signed_l = client.create_order(lo)
                        resp_l = client.post_order(signed_l, OrderType.GTC)
                        if resp_l and (resp_l.get("orderID") or resp_l.get("success")):
                            executed = True
                            logger.info(f"      ✅ GTC ejecutada")
                    except Exception as e:
                        logger.debug(f"      GTC falló: {str(e)[:60]}")

                if executed:
                    trade["status"] = "EXECUTED"
                    STATE.current_bankroll -= bet_amount
                    self.traded_markets.add(market_id)
                    self._save_bet(market_id, question)
                    STATE.total_trades += 1
                    STATE.open_positions += 1
                    logger.info(f"      ✅ Football trade! Capital: ${STATE.current_bankroll:.2f}")
                else:
                    trade["status"] = "FAILED"
                    self.traded_markets.add(market_id)
            except Exception as e:
                trade["status"] = "ERROR"
                trade["error"] = str(e)

        return trade

    # ═══════════════════════════════════════════════════════════════
    # PARSEAR PREGUNTA DEL PARTIDO
    # ═══════════════════════════════════════════════════════════════

    def _parse_match_question(self, question: str, market: Optional[Dict] = None) -> Dict:
        """Extrae información del partido de la pregunta.

        Si `market` viene enriquecido por `_find_football_markets` (campos
        `_subject`, `_team_a`, etc.) usa ese contexto directamente — es fiable
        porque sale del título del evento 'A vs. B'. El regex queda de fallback.
        """
        q = question.lower()
        if market and market.get("_subject") is not None:
            # team_a = equipo del "Yes" (sujeto), team_b = rival → P(team_a gana)=P(Yes)
            subject = market["_subject"]
            opponent = (market["_team_b"] if subject == market["_team_a"]
                        else market["_team_a"])
            return {
                "team_a": subject,
                "team_b": opponent,
                "question_type": "win",
                "league": "world cup" if market.get("_neutral") else None,
                "home_team": market.get("_home_team"),
                "neutral_venue": bool(market.get("_neutral")),
            }
        if market and market.get("_is_draw"):
            return {
                "team_a": market.get("_team_a"),
                "team_b": market.get("_team_b"),
                "question_type": "draw",
                "league": "world cup" if market.get("_neutral") else None,
                "home_team": market.get("_home_team"),
                "neutral_venue": bool(market.get("_neutral")),
            }
        result = {
            "team_a": None,
            "team_b": None,
            "question_type": "win",
            "league": None,
            "home_team": None,      # equipo local si se detecta
            "neutral_venue": False, # sin ventaja de local
        }

        # Detectar tipo de pregunta
        if "draw" in q or "tie" in q:
            result["question_type"] = "draw"
        elif "advance" in q or "qualify" in q or "progress" in q or "through" in q:
            result["question_type"] = "advance"
        elif any(x in q for x in ["beat", "win", "defeat", "score more"]):
            result["question_type"] = "win"

        # Detectar venue neutral (sin ventaja local)
        if any(kw in q for kw in NEUTRAL_VENUE_KW):
            result["neutral_venue"] = True

        # Detectar liga
        for lg in PRIORITY_LEAGUES:
            if lg in q:
                result["league"] = lg
                break

        # Parsear "Team A vs Team B" — team_a se asume local salvo indicación
        vs_patterns = [
            r"will\s+(.+?)\s+(?:beat|defeat|vs\.?|v)\s+(.+?)[\?$]",
            r"(.+?)\s+vs\.?\s+(.+?)[\?\s\|]",
            r"(.+?)\s+v\s+(.+?)[\?\s\|]",
        ]
        for pat in vs_patterns:
            m = re.search(pat, q)
            if m:
                result["team_a"] = m.group(1).strip()
                result["team_b"] = m.group(2).strip()
                break

        # Detectar local explícito ("at home", "home game")
        if result["team_a"] and ("at home" in q or "home" in q.split()):
            result["home_team"] = result["team_a"]
        elif result["team_a"] and not result["neutral_venue"]:
            # Por convención en Polymarket, el primer equipo suele ser local
            result["home_team"] = result["team_a"]

        return result

    # ═══════════════════════════════════════════════════════════════
    # ESTIMAR PROBABILIDAD VERDADERA
    # ═══════════════════════════════════════════════════════════════

    async def _estimate_true_probability(
        self, question: str, parsed: Dict, market_price: float
    ) -> Optional[float]:
        """
        Estima probabilidad real usando múltiples señales:
        1. Rating Elo de ClubElo.com (si hay equipos identificados)
        2. Ajustes por tipo de pregunta
        3. Señal de mercado (base)
        """
        q = question.lower()
        q_type = parsed.get("question_type", "win")
        team_a = parsed.get("team_a")
        team_b = parsed.get("team_b")

        # Base: confiar en el mercado como punto de partida
        base_prob = market_price

        # Si tenemos los dos equipos, intentar Elo
        if team_a and team_b and len(team_a) > 2 and len(team_b) > 2:
            elo_prob = await self._get_elo_probability(
                team_a, team_b, q_type,
                home_team=parsed.get("home_team"),
                neutral_venue=parsed.get("neutral_venue", False),
            )
            if elo_prob is not None:
                # Blend: 65% Elo + 35% mercado.
                # Más peso a Elo que antes (era 60/40) porque el modelo de
                # home advantage lo hace más preciso.
                blended = 0.65 * elo_prob + 0.35 * base_prob
                neutral_note = " [neutral]" if parsed.get("neutral_venue") else ""
                logger.info(f"      Elo={elo_prob:.2f}{neutral_note} | Market={base_prob:.2f} | Blend={blended:.2f}")
                return blended

        # Sin Elo: análisis heurístico de la pregunta
        return self._heuristic_probability(q, q_type, market_price)

    async def _get_elo_probability(
        self, team_a: str, team_b: str, q_type: str,
        home_team: str = None, neutral_venue: bool = False
    ) -> Optional[float]:
        """Obtiene ratings Elo de ClubElo.com y calcula probabilidad."""
        cache_key = f"elo:{team_a}:{team_b}:{q_type}:{home_team}"
        if cache_key in self.elo_cache:
            return self.elo_cache[cache_key]

        session = await self._get_session()

        # En venue internacional/neutral (Mundial) los equipos son SELECCIONES
        # → ClubElo no las tiene; usar eloratings.net. Para clubes, ClubElo.
        elo_a = elo_b = None
        if neutral_venue:
            elo_a = await self._fetch_national_elo(session, team_a)
            elo_b = await self._fetch_national_elo(session, team_b)
        if elo_a is None:
            elo_a = await self._fetch_club_elo(session, team_a)
        if elo_b is None:
            elo_b = await self._fetch_club_elo(session, team_b)

        if elo_a is None or elo_b is None:
            return None

        # Ventaja de local: +65 Elo al equipo local (estándar FIFA)
        # Si es terreno neutral (World Cup, Champions League final) → sin ventaja
        elo_a_adj = elo_a
        elo_b_adj = elo_b
        if not neutral_venue:
            if home_team and home_team in team_a.lower():
                elo_a_adj += HOME_ELO_ADVANTAGE
            elif home_team and home_team in team_b.lower():
                elo_b_adj += HOME_ELO_ADVANTAGE
            else:
                # Convención: primer equipo es local por defecto
                elo_a_adj += HOME_ELO_ADVANTAGE

        diff = elo_a_adj - elo_b_adj
        p_a_win = 1.0 / (1.0 + 10 ** (-diff / 400.0))

        # Probabilidad de empate: modelo Dixon-Coles simplificado
        # Empate es más probable cuanto más parecidos sean los equipos
        elo_diff_abs = abs(diff)
        if q_type == "draw":
            if elo_diff_abs < 50:
                prob = 0.30   # Equipos muy parejos
            elif elo_diff_abs < 150:
                prob = 0.27
            elif elo_diff_abs < 300:
                prob = 0.22
            else:
                prob = 0.16   # Diferencia grande → empate raro
        elif q_type == "win":
            # Restar prob de empate al favorito para ser conservadores
            draw_prob = max(0.16, 0.30 - elo_diff_abs / 2000)
            p_a_real = p_a_win * (1.0 - draw_prob)
            prob = p_a_real
        elif q_type == "advance":
            # Avanzar incluye también poder ganar en penales/prórrogas
            prob = min(0.92, p_a_win * 1.10)
        else:
            prob = p_a_win

        prob = max(0.05, min(0.95, prob))
        self.elo_cache[cache_key] = prob
        return prob

    @staticmethod
    def _normalize_team(team: str) -> str:
        """Normaliza nombre de equipo: minúsculas, sin acentos ni puntuación."""
        import unicodedata
        t = unicodedata.normalize("NFKD", team)
        t = "".join(c for c in t if not unicodedata.combining(c))
        t = re.sub(r"[^a-z0-9\s]", " ", t.lower())
        return re.sub(r"\s+", " ", t).strip()

    async def _fetch_national_elo(self, session: aiohttp.ClientSession,
                                  team: str) -> Optional[float]:
        """Rating Elo de una SELECCIÓN nacional vía eloratings.net (cacheado 6h)."""
        # Cargar/refrescar tabla
        if self._national_elo is None or (time.time() - self._national_elo_ts) > 21600:
            try:
                async with session.get(NATIONAL_ELO_URL,
                        headers={"User-Agent": "Mozilla/5.0"}) as r:
                    if r.status == 200:
                        text = await r.text()
                        table = {}
                        for line in text.splitlines():
                            parts = line.split("\t")
                            if len(parts) > 3:
                                try:
                                    table[parts[2]] = float(parts[3])
                                except ValueError:
                                    pass
                        if table:
                            self._national_elo = table
                            self._national_elo_ts = time.time()
                            logger.info(f"      🌍 eloratings.net: {len(table)} selecciones cargadas")
            except Exception as e:
                logger.debug(f"      eloratings fetch error: {e}")
        if not self._national_elo:
            return None

        norm = self._normalize_team(team)
        code = NATIONAL_TEAM_CODES.get(norm)
        if not code:
            # Probar quitando prefijos comunes ("ir iran" → "iran")
            for key, c in NATIONAL_TEAM_CODES.items():
                if norm == key or norm.endswith(" " + key) or norm.startswith(key + " "):
                    code = c
                    break
        if not code:
            return None
        elo = self._national_elo.get(code)
        if elo is not None:
            logger.info(f"      🌍 {team} ({code}): {elo:.0f}")
        return elo

    async def _fetch_club_elo(self, session: aiohttp.ClientSession, team: str) -> Optional[float]:
        """Obtiene rating Elo de un equipo desde ClubElo.com.
        Prueba múltiples formatos de nombre para aumentar el hit rate.
        """
        team_clean = re.sub(r'[^a-zA-Z0-9\s\-]', '', team).strip()

        # Generar variantes del nombre para probar
        variants = []
        base = team_clean.title().replace(' ', '_')
        variants.append(base)
        # Sin artículos comunes
        no_articles = re.sub(r'\b(The|El|La|Los|Las|De|Del|Fc|Cf|Sc|Ac|As)\b', '', team_clean, flags=re.I).strip()
        variants.append(no_articles.title().replace(' ', '_'))
        # Solo primera palabra (para equipos como "Real Madrid" → "Real_Madrid" ya está, pero "FC Barcelona" → "Barcelona")
        words = team_clean.split()
        if words:
            variants.append(words[-1].title())  # última palabra
            if len(words) > 1:
                variants.append('_'.join(w.title() for w in words[1:]))  # sin primera palabra

        # Alias manuales de selecciones nacionales (World Cup 2026)
        NATIONAL_ALIASES = {
            # América del Norte (sede)
            "usa": "USA", "united states": "USA", "us": "USA",
            "mexico": "Mexico", "canada": "Canada",
            # Europa
            "england": "England", "france": "France", "germany": "Germany",
            "spain": "Spain", "portugal": "Portugal", "netherlands": "Netherlands",
            "holland": "Netherlands", "italy": "Italy", "croatia": "Croatia",
            "serbia": "Serbia", "switzerland": "Switzerland", "austria": "Austria",
            "belgium": "Belgium", "denmark": "Denmark", "poland": "Poland",
            "ukraine": "Ukraine", "hungary": "Hungary", "scotland": "Scotland",
            "turkey": "Turkey", "czechia": "CzechRepublic", "czech republic": "CzechRepublic",
            "slovakia": "Slovakia", "romania": "Romania", "wales": "Wales",
            "greece": "Greece", "norway": "Norway",
            # América del Sur
            "brazil": "Brazil", "argentina": "Argentina", "colombia": "Colombia",
            "uruguay": "Uruguay", "chile": "Chile", "ecuador": "Ecuador",
            "peru": "Peru", "venezuela": "Venezuela", "bolivia": "Bolivia",
            "paraguay": "Paraguay",
            # África
            "morocco": "Morocco", "senegal": "Senegal", "nigeria": "Nigeria",
            "ghana": "Ghana", "egypt": "Egypt", "ivory coast": "IvoryCoast",
            "cameroon": "Cameroon", "mali": "Mali", "south africa": "SouthAfrica",
            "tunisia": "Tunisia", "algeria": "Algeria",
            # Asia / Oceanía
            "japan": "Japan", "south korea": "SouthKorea", "korea": "SouthKorea",
            "australia": "Australia", "iran": "Iran", "saudi arabia": "SaudiArabia",
            "qatar": "Qatar", "iraq": "Iraq", "uzbekistan": "Uzbekistan",
            # CONCACAF
            "costa rica": "CostaRica", "panama": "Panama", "honduras": "Honduras",
            "jamaica": "Jamaica", "el salvador": "ElSalvador",
        }
        team_lower = team.lower().strip()
        if team_lower in NATIONAL_ALIASES:
            variants.insert(0, NATIONAL_ALIASES[team_lower])

        # Probar cada variante
        for slug in dict.fromkeys(variants):  # dedup preservando orden
            if not slug or slug == '_':
                continue
            cache_key = f"clubelo:{slug}"
            if cache_key in self.elo_cache:
                return self.elo_cache[cache_key]
            try:
                url = f"http://api.clubelo.com/{slug}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        lines = [l for l in text.strip().split('\n') if l and not l.startswith('Rank')]
                        if lines:
                            parts = lines[-1].split(',')
                            if len(parts) >= 5:
                                try:
                                    elo = float(parts[4])
                                    self.elo_cache[cache_key] = elo
                                    logger.info(f"      ClubElo {slug}: {elo:.0f}")
                                    return elo
                                except ValueError:
                                    pass
            except Exception as e:
                logger.debug(f"      ClubElo error {slug}: {e}")

        return None

    def _heuristic_probability(self, q: str, q_type: str, market_price: float) -> Optional[float]:
        """
        Heurísticas cuando no hay datos Elo:
        - Draws tienden a tener P~0.25-0.30 independiente del mercado
        - Favoritos extremos (>80%) suelen estar sobrevaluados
        - Underdogs extremos (<20%) suelen estar subvaluados
        """
        if q_type == "draw":
            # El mercado suele sobrevalorar draws en equipos parecidos
            # Promedio histórico de empate en fútbol: ~26%
            if market_price > 0.35:
                return 0.30  # Mercado sobrevalora empate
            elif market_price < 0.15:
                return 0.22  # Empates nunca son tan raros
            return market_price  # En rango razonable, confiar

        if q_type == "win":
            if market_price > 0.82:
                # Favorito extremo: mercado suele sobrevalorar
                # Revertir levemente hacia la media
                return market_price * 0.94
            elif market_price < 0.20:
                # Underdog extremo: puede tener más valor del que muestra
                return market_price * 1.12
            return market_price

        # Para advance/qualify, confiar más en el mercado
        return market_price

    # ═══════════════════════════════════════════════════════════════
    # CLASIFICAR TIPO DE APUESTA
    # ═══════════════════════════════════════════════════════════════

    def _classify_bet(self, market_price: float, true_prob: float) -> str:
        """Clasifica la apuesta para determinar el edge mínimo requerido."""
        if market_price < 0.33:
            return "underdog"
        elif market_price > 0.65:
            return "favorite"
        else:
            return "even"

    def _get_min_edge(self, bet_type: str) -> float:
        if bet_type == "underdog":
            return MIN_EDGE_UNDERDOG
        elif bet_type == "favorite":
            return MIN_EDGE_FAVORITE
        else:
            return MIN_EDGE_DRAW

    def get_stats(self) -> str:
        return f"⚽ Football Trader: ligas {', '.join(PRIORITY_LEAGUES[:4])}..."
