"""
PolyBot - Strategy 4: Weather Trader
======================================
Usa Open-Meteo API (gratis, sin key) para tradear mercados de clima.
Cross-verifica 3+ modelos meteorológicos antes de apostar.

Inspirado en: gopfan2 ($2M+), 1pixel ($18.5K de $2.3K)
Edge mínimo: 15%

Ciudades: NYC, Chicago, LA, Miami, Seattle, Atlanta, London + más
"""

import os
import re
import json
import math
import time
import logging
import asyncio
import aiohttp
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timezone, timedelta
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.weather")

GAMMA_API_URL = "https://gamma-api.polymarket.com"

# ── Ciudades monitoreadas ───────────────────────────────────────────
CITIES = {
    "new york":      {"lat": 40.71, "lon": -74.01, "aliases": ["nyc", "new york", "new york city", "manhattan"]},
    "chicago":       {"lat": 41.88, "lon": -87.63, "aliases": ["chicago"]},
    "los angeles":   {"lat": 34.05, "lon": -118.24, "aliases": ["los angeles", "l.a."]},
    "miami":         {"lat": 25.76, "lon": -80.19, "aliases": ["miami"]},
    "seattle":       {"lat": 47.61, "lon": -122.33, "aliases": ["seattle"]},
    "atlanta":       {"lat": 33.75, "lon": -84.39, "aliases": ["atlanta"]},
    "london":        {"lat": 51.51, "lon": -0.13, "aliases": ["london", "londres"]},
    "washington":    {"lat": 38.91, "lon": -77.04, "aliases": ["washington", "dc", "washington dc", "d.c."]},
    "denver":        {"lat": 39.74, "lon": -104.99, "aliases": ["denver"]},
    "phoenix":       {"lat": 33.45, "lon": -112.07, "aliases": ["phoenix"]},
    "houston":       {"lat": 29.76, "lon": -95.37, "aliases": ["houston"]},
    "san francisco": {"lat": 37.77, "lon": -122.42, "aliases": ["san francisco", "sf"]},
    "dallas":        {"lat": 32.78, "lon": -96.80, "aliases": ["dallas"]},
    "austin":        {"lat": 30.27, "lon": -97.74, "aliases": ["austin"]},
    "las vegas":     {"lat": 36.17, "lon": -115.14, "aliases": ["las vegas", "vegas"]},
    "minneapolis":   {"lat": 44.98, "lon": -93.27, "aliases": ["minneapolis"]},
    "boston":         {"lat": 42.36, "lon": -71.06, "aliases": ["boston"]},
    "detroit":       {"lat": 42.33, "lon": -83.05, "aliases": ["detroit"]},
    "nashville":     {"lat": 36.16, "lon": -86.78, "aliases": ["nashville"]},
    "portland":      {"lat": 45.52, "lon": -122.68, "aliases": ["portland"]},
    "charlotte":     {"lat": 35.23, "lon": -80.84, "aliases": ["charlotte"]},
    "philadelphia":  {"lat": 39.95, "lon": -75.17, "aliases": ["philadelphia", "philly"]},
    "orlando":       {"lat": 28.54, "lon": -81.38, "aliases": ["orlando"]},
    "sacramento":    {"lat": 38.58, "lon": -121.49, "aliases": ["sacramento"]},
    "san diego":     {"lat": 32.72, "lon": -117.16, "aliases": ["san diego"]},
    "pittsburgh":    {"lat": 40.44, "lon": -79.99, "aliases": ["pittsburgh"]},
    "st louis":      {"lat": 38.63, "lon": -90.20, "aliases": ["st. louis", "st louis", "saint louis"]},
    "kansas city":   {"lat": 39.10, "lon": -94.58, "aliases": ["kansas city"]},
    "salt lake city":{"lat": 40.76, "lon": -111.89, "aliases": ["salt lake city", "salt lake"]},
    "new orleans":   {"lat": 29.95, "lon": -90.07, "aliases": ["new orleans"]},
    "cleveland":     {"lat": 41.50, "lon": -81.69, "aliases": ["cleveland"]},
    "indianapolis":  {"lat": 39.77, "lon": -86.16, "aliases": ["indianapolis"]},
    "raleigh":       {"lat": 35.78, "lon": -78.64, "aliases": ["raleigh"]},
    "tampa":         {"lat": 27.95, "lon": -82.46, "aliases": ["tampa"]},
    "milwaukee":     {"lat": 43.04, "lon": -87.91, "aliases": ["milwaukee"]},
}

# Modelos meteorológicos de Open-Meteo
WEATHER_MODELS = [
    "gfs_seamless",        # NOAA GFS (USA)
    "ecmwf_ifs025",        # ECMWF (Europa, gold standard)
    "jma_seamless",        # JMA (Japón)
    "icon_seamless",       # DWD ICON (Alemania)
    "gem_seamless",        # CMC GEM (Canadá)
    "meteofrance_seamless" # Météo-France
]

MIN_MODELS_AGREE = 3   # 3 de 6 modelos de acuerdo (balance entre seguridad y oportunidad)
MIN_EDGE = 0.18  # 18% mínimo (entre el 15% original y 20% que era muy estricto)


class WeatherTrader:
    """Estrategia de trading basada en pronósticos meteorológicos."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache = {}         # (city, date, metric) → {data, ts}
        self.cache_ttl = 600    # 10 min
        self.last_run = 0
        self.min_interval = 300  # 5 min entre escaneos
        self.harvested = set()
        self._load_harvested()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _load_harvested(self):
        try:
            with open("data/bets_placed.json", "r") as f:
                data = json.load(f)
                self.harvested = set(data.get("market_ids", []))
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
                    "strategy": "WEATHER"
                })
            with open("data/bets_placed.json", "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # PUNTO DE ENTRADA: run_cycle()
    # ═══════════════════════════════════════════════════════════════

    async def run_cycle(self) -> Optional[Dict]:
        """
        Ejecuta un ciclo: busca mercados de clima, analiza con modelos
        meteorológicos, y ejecuta si hay edge >15%.
        
        Retorna trade dict o None.
        """
        if STATE.is_paused:
            return None

        now = time.time()
        if now - self.last_run < self.min_interval:
            return None
        self.last_run = now

        logger.info("⛅ Weather Trader: Buscando mercados de clima...")

        # 1. Buscar mercados de clima en Polymarket
        weather_markets = await self._find_weather_markets()
        if not weather_markets:
            logger.info("   ⛅ No se encontraron mercados de clima activos")
            return None

        logger.info(f"   ⛅ Encontrados {len(weather_markets)} mercados de clima")

        # 2. Analizar cada mercado (máx 10 para no gastar tiempo)
        for market in weather_markets[:10]:
            try:
                trade = await self._analyze_and_trade(market)
                if trade:
                    return trade  # Un trade por ciclo máximo
            except Exception as e:
                logger.error(f"   ⛅ Error analizando: {e}")

        logger.info("   ⛅ Sin oportunidades de clima en este ciclo")
        return None

    # ═══════════════════════════════════════════════════════════════
    # BUSCAR MERCADOS DE CLIMA
    # ═══════════════════════════════════════════════════════════════

    async def _find_weather_markets(self) -> List[Dict]:
        """Busca mercados de clima activos en Polymarket."""
        session = await self._get_session()
        weather_keywords = [
            "temperature", "temp", "degrees", "°f", "°c",
            "rain", "snow", "precipitation", "weather",
            "high of", "low of", "above", "below",
            "warmer", "colder", "heat", "freeze"
        ]

        markets = []
        for offset in [0, 100, 200, 300, 400, 500]:
            try:
                async with session.get(
                    f"{GAMMA_API_URL}/markets",
                    params={
                        "active": "true", "closed": "false",
                        "limit": 100, "offset": str(offset),
                        "order": "volume", "ascending": "false"
                    }
                ) as resp:
                    if resp.status == 200:
                        batch = await resp.json()
                        if not batch:
                            break
                        for m in batch:
                            q = (m.get("question") or "").lower()
                            if any(kw in q for kw in weather_keywords):
                                market_id = str(m.get("id", ""))
                                cond_id = m.get("conditionId", "")
                                if market_id in self.harvested or cond_id in self.harvested:
                                    continue

                                # Filtro: resuelve en max 3 días
                                end_str = m.get("endDate", "")
                                if end_str:
                                    try:
                                        end_dt = datetime.fromisoformat(
                                            end_str.replace("Z", "+00:00"))
                                        days = (end_dt - datetime.now(timezone.utc)).days
                                        if days < 0 or days > 3:
                                            continue
                                    except:
                                        pass

                                markets.append(m)
            except Exception:
                break

        return markets

    # ═══════════════════════════════════════════════════════════════
    # ANALIZAR Y TRADEAR
    # ═══════════════════════════════════════════════════════════════

    async def _analyze_and_trade(self, market: Dict) -> Optional[Dict]:
        """Analiza un mercado de clima y ejecuta si hay edge."""
        question = market.get("question", "")
        market_id = str(market.get("id", ""))

        # 1. Parsear la pregunta
        parsed = self._parse_weather_question(question)
        if not parsed:
            return None

        city = parsed["city"]
        date = parsed["date"]
        metric = parsed["metric"]
        threshold = parsed["threshold"]
        direction = parsed["direction"]
        threshold_high = parsed.get("threshold_high")

        logger.info(f"   ⛅ {question[:55]}")
        if direction == "between":
            logger.info(f"      City={city}, Date={date}, {metric} BETWEEN {threshold}-{threshold_high}")
        else:
            logger.info(f"      City={city}, Date={date}, {metric} {direction} {threshold}")

        # 2. Obtener pronósticos multi-modelo
        forecasts = await self._get_multi_model_forecast(city, date, metric)
        if len(forecasts) < MIN_MODELS_AGREE:
            logger.info(f"      Solo {len(forecasts)} modelos respondieron (mín {MIN_MODELS_AGREE})")
            return None

        # 3. Calcular probabilidad
        prob_yes = self._calculate_probability(forecasts, threshold, direction, metric, threshold_high)
        logger.info(f"      Prob YES={prob_yes:.1%} ({len(forecasts)} modelos)")

        # 4. Comparar con precio de mercado
        outcomes = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str):
            prices = json.loads(outcomes)
        else:
            prices = outcomes
        if len(prices) < 2:
            return None

        yes_price = float(prices[0])
        no_price = float(prices[1])

        # VALIDACIÓN: rechazar precios inválidos (0, cerca de 0, o cerca de 1)
        if yes_price < 0.02 or yes_price > 0.98:
            logger.info(f"      Precio YES={yes_price:.2f} fuera de rango, skip")
            return None
        if no_price < 0.02 or no_price > 0.98:
            logger.info(f"      Precio NO={no_price:.2f} fuera de rango, skip")
            return None

        tokens = market.get("clobTokenIds", "[]")
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if len(tokens) < 2:
            return None

        # 5. Calcular edge
        edge_yes = prob_yes - yes_price
        edge_no = (1 - prob_yes) - no_price

        if edge_yes > edge_no and edge_yes >= MIN_EDGE:
            side = "YES"
            edge = edge_yes
            price = yes_price
            token_id = tokens[0]
        elif edge_no >= MIN_EDGE:
            side = "NO"
            edge = edge_no
            price = no_price
            token_id = tokens[1]
        else:
            logger.info(f"      Edge YES={edge_yes:+.1%}, NO={edge_no:+.1%} → insuficiente (<{MIN_EDGE:.0%})")
            return None

        logger.info(f"      🎯 EDGE {side}: {edge:.1%} (precio mercado: {price:.2f})")

        # 6. Calcular monto
        bet_amount = min(
            STATE.current_bankroll * 0.06,
            SAFETY.max_bet_absolute,
            STATE.current_bankroll * 0.10
        )
        bet_amount = max(bet_amount, 2.0)
        bet_amount = round(bet_amount, 2)

        # 7. Ejecutar
        trade = {
            "strategy": "WEATHER",
            "timestamp": datetime.now().isoformat(),
            "market_id": market_id,
            "question": question,
            "side": side,
            "amount": bet_amount,
            "price": price,
            "edge": edge,
            "probability": prob_yes if side == "YES" else 1 - prob_yes,
            "models_used": len(forecasts),
            "forecast_values": {m: round(v, 1) for m, v in forecasts.items()},
            "city": city,
            "metric": metric,
            "threshold": threshold,
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
                    self.harvested.add(market_id)
                    self._save_bet(market_id, question)
                    STATE.total_trades += 1
                    STATE.open_positions += 1
                    logger.info(f"      ✅ Weather trade ejecutado! Capital: ${STATE.current_bankroll:.2f}")
                else:
                    trade["status"] = "FAILED"
                    # Marcar para no reintentar
                    self.harvested.add(market_id)
            except Exception as e:
                trade["status"] = "ERROR"
                trade["error"] = str(e)
                logger.error(f"      ❌ Error: {e}")

        return trade

    # ═══════════════════════════════════════════════════════════════
    # PARSEAR PREGUNTA DE CLIMA
    # ═══════════════════════════════════════════════════════════════

    def _parse_weather_question(self, question: str) -> Optional[Dict]:
        """
        Parsea preguntas como:
        - "Will the high temperature in NYC on April 5 be above 65°F?"
        - "Will it rain in Chicago tomorrow?"
        - "NYC high temperature April 5: Above or below 58°F?"
        """
        q = question.lower().strip()

        # 1. Ciudad (word boundary matching para evitar 'la' match 'dallas')
        city = None
        best_match_len = 0  # Preferir el alias más largo
        for city_name, info in CITIES.items():
            for alias in info["aliases"]:
                # Usar regex word boundary para match exacto
                pattern = r'(?:^|[\s,;:\-\(\)])' + re.escape(alias) + r'(?:$|[\s,;:\-\(\)\'\"?!.])'
                if re.search(pattern, q):
                    if len(alias) > best_match_len:
                        city = city_name
                        best_match_len = len(alias)
        if not city:
            return None

        # 2. Fecha
        date = self._parse_date(q)
        if not date:
            return None

        # 3. Métrica
        if any(w in q for w in ["high temp", "high of", "maximum", "high will"]):
            metric = "temperature_2m_max"
        elif any(w in q for w in ["low temp", "low of", "minimum", "low will", "low in", "drop below"]):
            metric = "temperature_2m_min"
        elif any(w in q for w in ["rain", "precipitation", "inches of rain"]):
            metric = "precipitation_sum"
        elif any(w in q for w in ["snow", "snowfall"]):
            metric = "snowfall_sum"
        elif any(w in q for w in ["wind", "mph", "wind speed"]):
            metric = "wind_speed_10m_max"
        elif "temp" in q or "degree" in q or "°" in q:
            metric = "temperature_2m_max"
        else:
            return None

        # 4. Threshold — PRIMERO checar si es "between X-Y" (rango estrecho)
        threshold = None
        threshold_high = None
        direction = "above"  # default

        # Detectar "between" patterns: "between 74-75", "between 68 and 69", "between $74-$75"
        between_patterns = [
            r'between\s+(\d+\.?\d*)\s*[-–]\s*(\d+\.?\d*)',     # between 74-75
            r'between\s+\$?(\d+\.?\d*)\s+and\s+\$?(\d+\.?\d*)', # between 74 and 75
            r'between\s+(\d+\.?\d*)\s*°?\s*[fFcC]?\s*[-–]\s*(\d+\.?\d*)', # between 74°F-75°F
            r'between\s+(\d+\.?\d*)\s*°?\s*[fFcC]?\s+and\s+(\d+\.?\d*)',  # between 74°F and 75
        ]
        for pattern in between_patterns:
            match = re.search(pattern, q)
            if match:
                threshold = float(match.group(1))       # low end
                threshold_high = float(match.group(2))   # high end
                # Asegurar que low < high
                if threshold > threshold_high:
                    threshold, threshold_high = threshold_high, threshold
                direction = "between"
                break

        # Si no es "between", parsear threshold normal
        if threshold is None:
            patterns = [
                r'(\d+\.?\d*)\s*°?\s*[fF]', r'above\s+(\d+\.?\d*)',
                r'below\s+(\d+\.?\d*)', r'over\s+(\d+\.?\d*)',
                r'under\s+(\d+\.?\d*)', r'exceed\s+(\d+\.?\d*)',
                r'(\d+\.?\d*)\s*degrees', r'(?:reach|hit)\s+(\d+\.?\d*)',
            ]
            for pattern in patterns:
                match = re.search(pattern, q)
                if match:
                    threshold = float(match.group(1))
                    break
            if threshold is None:
                return None

            # 5. Dirección (solo si no es "between")
            if any(w in q for w in ["below", "under", "less than", "drop below", "lower", "colder"]):
                direction = "below"
            else:
                direction = "above"

        # Unidad
        use_celsius = "°c" in q or "celsius" in q
        unit = "celsius" if use_celsius else "fahrenheit"

        result = {
            "city": city, "date": date, "metric": metric,
            "threshold": threshold, "direction": direction, "unit": unit
        }
        if threshold_high is not None:
            result["threshold_high"] = threshold_high
        return result

    def _parse_date(self, q: str) -> Optional[str]:
        """Extrae fecha de la pregunta → YYYY-MM-DD."""
        now = datetime.now(timezone.utc)

        if "today" in q:
            return now.strftime("%Y-%m-%d")
        if "tomorrow" in q:
            return (now + timedelta(days=1)).strftime("%Y-%m-%d")

        months = {
            "january": 1, "jan": 1, "february": 2, "feb": 2,
            "march": 3, "mar": 3, "april": 4, "apr": 4,
            "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
            "august": 8, "aug": 8, "september": 9, "sep": 9,
            "october": 10, "oct": 10, "november": 11, "nov": 11,
            "december": 12, "dec": 12,
        }

        for name, month in months.items():
            match = re.search(rf'{name}\s+(\d{{1,2}})', q)
            if match:
                day = int(match.group(1))
                try:
                    target = datetime(now.year, month, day, tzinfo=timezone.utc)
                    if target.date() < now.date() - timedelta(days=7):
                        target = datetime(now.year + 1, month, day, tzinfo=timezone.utc)
                    return target.strftime("%Y-%m-%d")
                except ValueError:
                    continue

        # "4/5" formato
        match = re.search(r'(\d{1,2})/(\d{1,2})', q)
        if match:
            try:
                target = datetime(now.year, int(match.group(1)),
                                  int(match.group(2)), tzinfo=timezone.utc)
                return target.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # Default: mañana
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")

    # ═══════════════════════════════════════════════════════════════
    # PRONÓSTICOS MULTI-MODELO
    # ═══════════════════════════════════════════════════════════════

    async def _get_multi_model_forecast(self, city: str, date: str,
                                         metric: str) -> Dict[str, float]:
        """
        Consulta Open-Meteo con múltiples modelos + GFS 31-member ensemble.
        El ensemble da 31 predicciones independientes para mayor precisión.
        """
        cache_key = f"{city}:{date}:{metric}"
        if cache_key in self.cache:
            c = self.cache[cache_key]
            if time.time() - c["ts"] < self.cache_ttl:
                return c["data"]

        info = CITIES.get(city, {})
        lat, lon = info.get("lat", 0), info.get("lon", 0)
        use_f = city != "london"

        forecasts = {}
        session = await self._get_session()

        # Método 1: GFS 31-member ensemble (más preciso, como los bots de $24K)
        try:
            # Mapear daily metrics a hourly para ensemble
            hourly_metric = metric.replace("temperature_2m_max", "temperature_2m").replace(
                "temperature_2m_min", "temperature_2m").replace(
                "precipitation_sum", "precipitation").replace(
                "snowfall_sum", "snowfall").replace(
                "wind_speed_10m_max", "wind_speed_10m")

            params = {
                "latitude": lat, "longitude": lon,
                "hourly": hourly_metric,
                "temperature_unit": "fahrenheit" if use_f else "celsius",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "start_date": date, "end_date": date,
                "models": "gfs_seamless",
            }
            async with session.get(
                "https://ensemble-api.open-meteo.com/v1/ensemble",
                params=params
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    hourly = data.get("hourly", {})
                    # Buscar todas las keys que sean del metric (member_0, member_1, etc.)
                    member_values = {}
                    for key, values in hourly.items():
                        if key.startswith(hourly_metric) and values:
                            # Para max temp: tomar el máximo del día de cada miembro
                            valid = [v for v in values if v is not None]
                            if valid:
                                if "max" in metric or "temperature" in metric:
                                    member_values[key] = max(valid)
                                elif "min" in metric:
                                    member_values[key] = min(valid)
                                else:
                                    member_values[key] = sum(valid)  # precipitation sum

                    if member_values:
                        for name, val in member_values.items():
                            forecasts[f"ensemble_{name}"] = val
                        logger.debug(f"   GFS ensemble: {len(member_values)} members loaded")
        except Exception as e:
            logger.debug(f"   GFS ensemble failed: {e}")

        # Método 2: Modelos individuales (fallback y complemento)
        for model in WEATHER_MODELS:
            try:
                params = {
                    "latitude": lat, "longitude": lon,
                    "daily": metric,
                    "temperature_unit": "fahrenheit" if use_f else "celsius",
                    "wind_speed_unit": "mph",
                    "precipitation_unit": "inch",
                    "timezone": "auto",
                    "start_date": date, "end_date": date,
                    "models": model,
                }
                async with session.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params=params
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        values = data.get("daily", {}).get(metric, [])
                        if values and values[0] is not None:
                            forecasts[model] = float(values[0])
            except Exception as e:
                logger.debug(f"   Model {model} failed: {e}")

        self.cache[cache_key] = {"data": forecasts, "ts": time.time()}
        return forecasts

    # ═══════════════════════════════════════════════════════════════
    # CALCULAR PROBABILIDAD
    # ═══════════════════════════════════════════════════════════════

    def _calculate_probability(self, forecasts: Dict[str, float],
                                threshold: float, direction: str,
                                metric: str, threshold_high: float = None) -> float:
        """Calcula P(YES) usando ensemble de modelos + error típico."""
        values = list(forecasts.values())
        n = len(values)
        if n == 0:
            return 0.5

        mean = sum(values) / n

        # Desviación del ensemble
        if n > 1:
            variance = sum((v - mean) ** 2 for v in values) / (n - 1)
            std_ensemble = variance ** 0.5
        else:
            std_ensemble = 0

        # Error típico del forecast (1-2 días)
        typical_error = {
            "temperature_2m_max": 2.5,
            "temperature_2m_min": 3.0,
            "precipitation_sum": 0.15,
            "snowfall_sum": 1.0,
            "wind_speed_10m_max": 5.0,
        }
        base_err = typical_error.get(metric, 3.0)
        total_std = max(std_ensemble, base_err)

        # CDF normal aproximada
        z_low = (threshold - mean) / total_std if total_std > 0 else 0
        prob_below_low = self._normal_cdf(z_low)

        if direction == "between" and threshold_high is not None:
            # P(between low and high) = P(above low) - P(above high)
            z_high = (threshold_high - mean) / total_std if total_std > 0 else 0
            prob_below_high = self._normal_cdf(z_high)
            prob_between = prob_below_high - prob_below_low
            return max(0.0, min(1.0, prob_between))
        elif direction == "above":
            return 1 - prob_below_low
        else:  # below
            return prob_below_low

    @staticmethod
    def _normal_cdf(z: float) -> float:
        """Aproximación CDF normal (Abramowitz & Stegun)."""
        if z < -6: return 0.0
        if z > 6: return 1.0
        a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
        p = 0.3275911
        sign = 1 if z >= 0 else -1
        z_abs = abs(z) / math.sqrt(2)
        t = 1.0 / (1.0 + p * z_abs)
        y = 1.0 - (((((a5*t + a4)*t) + a3)*t + a2)*t + a1) * t * math.exp(-z_abs*z_abs)
        return 0.5 * (1.0 + sign * y)

    # ═══════════════════════════════════════════════════════════════
    # EJECUCIÓN REAL
    # ═══════════════════════════════════════════════════════════════

    async def _execute_real_order(self, token_id: str, price: float,
                                   amount: float) -> bool:
        """Ejecuta orden real en CLOB (mismo patrón que btc_15min)."""
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

            # FOK primero
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

            # GTC fallback
            try:
                limit_price = min(price + 0.02, 0.98)
                size = round(amount / max(price, 0.01), 2)
                lo = OrderArgs(
                    token_id=token_id,
                    price=round(limit_price, 2),
                    size=size, side=BUY
                )
                signed_l = client.create_order(lo)
                resp_l = client.post_order(signed_l, OrderType.GTC)
                if resp_l and isinstance(resp_l, dict):
                    oid = resp_l.get("orderID", "")
                    if oid or resp_l.get("success"):
                        logger.info(f"      ✅ GTC ejecutada: {oid[:20]}...")
                        return True
            except Exception as e:
                logger.debug(f"      GTC falló: {str(e)[:60]}")

            logger.warning("      ❌ FOK y GTC fallaron")
            return False

        except Exception as e:
            logger.error(f"      Error CLOB: {e}")
            return False

    def get_stats(self) -> str:
        return f"⛅ Weather: {len(self.cache)} cached, {len(CITIES)} ciudades, {len(WEATHER_MODELS)} modelos"
