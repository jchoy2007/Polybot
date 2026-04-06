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
from modules.btc_15min import BTC15MinStrategy
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
                    btc_strategy: BTC15MinStrategy,
                    redeemer: AutoRedeemer,
                    harvester: NOHarvester,
                    tracker: WinRateTracker,
                    weather_trader: WeatherTrader,
                    stock_trader: StockTrader,
                    telegram: TelegramMonitor = None,
                    scan_only: bool = False):
    """Ejecuta un ciclo completo con TODAS las estrategias."""

    cycle_start = datetime.now()
    
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

    # --- Paso 0: Sincronizar open_positions desde posiciones reales ---
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
        if risk.check_cooldown_expired():
            STATE.is_paused = False
            STATE.pause_reason = ""
            logger.info("✅ Cooldown expirado, bot reanudado")
        else:
            logger.info(f"   Espera {SAFETY.cooldown_hours_after_stoploss}h antes de reanudar")
            return

    # --- Paso 2: Escanear mercados ---
    logger.info("📊 Paso 1/4: Escaneando mercados...")
    markets = await scanner.scan_all_markets()

    if not markets:
        logger.warning("No se encontraron mercados que cumplan los filtros")
        return

    logger.info(f"   Encontrados {len(markets)} mercados elegibles")

    if scan_only:
        logger.info("\n📋 TOP 10 MERCADOS MÁS PRÓXIMOS A RESOLVERSE:")
        for i, m in enumerate(markets[:10], 1):
            logger.info(
                f"   {i}. [{m.days_until_resolution}d] {m.question[:50]}\n"
                f"      YES: ${m.outcome_yes_price:.3f} | "
                f"Liquidez: ${m.liquidity:,.0f} | "
                f"Vol: ${m.volume:,.0f}"
            )
        return

    # --- Paso 3: Analizar con IA ---
    logger.info("🧠 Paso 2/4: Analizando mercados con IA...")
    analyses = await analyzer.analyze_markets_batch(
        markets, max_to_analyze=10
    )

    if not analyses:
        logger.info("   Sin análisis nuevos de IA en este ciclo")
    else:
        # --- Paso 4: Filtrar por edge y decidir ---
        logger.info("📐 Paso 3/4: Evaluando oportunidades...")
        bets_to_place = []

        # Reset daily spend tracker si es nuevo día
        today = datetime.now().strftime("%Y-%m-%d")
        if STATE.daily_spend_date != today:
            STATE.daily_spend = 0.0
            STATE.daily_spend_date = today
        STATE.cycle_bets = 0

        # Crear lookup de datos de mercado para pasar liquidez/volumen reales
        market_lookup = {m.market_id: m for m in markets}

        for analysis in analyses:
            # === FILTRO: Si la IA dijo SKIP, respetar su decisión ===
            if hasattr(analysis, 'side') and analysis.side.upper() == "SKIP":
                logger.info(f"   ⏭️ {analysis.question[:40]}: IA dijo SKIP (lado)")
                continue
            if hasattr(analysis, 'recommended_action') and analysis.recommended_action.upper() == "SKIP":
                logger.info(f"   ⏭️ {analysis.question[:40]}: IA recomienda SKIP")
                continue

            mkt = market_lookup.get(analysis.market_id)
            mkt_liquidity = mkt.liquidity if mkt else 50000
            mkt_volume = mkt.volume if mkt else 50000
            mkt_category = mkt.category if mkt else "general"

            # === FILTRO: Resolución máxima 48 horas ===
            if mkt and hasattr(mkt, 'end_date') and mkt.end_date:
                try:
                    from datetime import timezone
                    end_str = mkt.end_date
                    if end_str.endswith("Z"):
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    elif "+" in end_str[-6:]:
                        end_dt = datetime.fromisoformat(end_str)
                    else:
                        end_dt = datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc)
                    hours_to_resolve = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                    if hours_to_resolve > 48:
                        logger.info(f"   ❌ {analysis.question[:40]}: Resuelve en {hours_to_resolve/24:.0f} días (máx 2)")
                        continue
                    if hours_to_resolve < 0:
                        continue
                except:
                    pass
            else:
                # Sin fecha de resolución = skip para IA (no apostar sin saber cuándo)
                q_lower = analysis.question.lower() if hasattr(analysis, 'question') else ""
                slow_kw = ["billboard", "weekly", "monthly", "season", "by april 30", "by may", "annual"]
                if any(kw in q_lower for kw in slow_kw):
                    logger.info(f"   ❌ {analysis.question[:40]}: Keyword lento detectado")
                    continue

            # === FILTRO: Máximo apuestas por ciclo ===
            if STATE.cycle_bets >= SAFETY.max_bets_per_cycle:
                logger.info(f"   ⏸️ Límite de {SAFETY.max_bets_per_cycle} apuestas/ciclo alcanzado")
                break

            # === FILTRO: Máximo gasto diario ===
            if STATE.daily_spend >= SAFETY.max_daily_spend:
                logger.info(f"   ⏸️ Límite diario de ${SAFETY.max_daily_spend:.0f} alcanzado (gastado: ${STATE.daily_spend:.2f})")
                break

            should_bet, reason, amount = risk.should_bet(
                estimated_prob=analysis.estimated_probability,
                market_price=analysis.market_price,
                market_liquidity=mkt_liquidity,
                market_volume=mkt_volume,
                category=mkt_category
            )

            if should_bet:
                if STATE.daily_spend + amount > SAFETY.max_daily_spend:
                    amount = round(SAFETY.max_daily_spend - STATE.daily_spend, 2)
                    if amount < SAFETY.min_bet_size:
                        logger.info(f"   ⏸️ Presupuesto diario agotado")
                        break

                bets_to_place.append((analysis, amount))
                STATE.cycle_bets += 1
                STATE.daily_spend += amount
                logger.info(f"   ✅ {reason}")
            else:
                logger.info(f"   ❌ {analysis.question[:40]}: {reason}")

        if bets_to_place:
            logger.info(f"🎯 Ejecutando {len(bets_to_place)} apuestas de valor...")
            for analysis, amount in bets_to_place:
                result = await executor.execute_bet(analysis, amount)
                status = result.get('status', 'UNKNOWN')
                logger.info(f"   Orden: {status}")
                if status in ("EXECUTED", "SIMULATED"):
                    tracker.add_trade(
                        market_id=str(analysis.market_id),
                        question=analysis.question,
                        side=analysis.side,
                        amount=amount,
                        price=analysis.market_price,
                        strategy="IA"
                    )
                    if telegram and status == "EXECUTED":
                        _mkt_ia = market_lookup.get(analysis.market_id)
                        _rt_ia = _get_resolve_time(_mkt_ia.end_date if _mkt_ia else "")
                        await telegram.send_trade_alert(
                            "IA", analysis.question, analysis.side,
                            amount, analysis.market_price, analysis.edge, _rt_ia)
                        telegram.log_trade("IA", analysis.question, analysis.side, amount)
                        logger.info(f"   ⏳ Resuelve en: {_rt_ia or 'desconocido'}")
        else:
            logger.info("   Sin apuestas de valor en este ciclo")

    # ===== ESTRATEGIA 2: BTC 15-Min Up/Down =====
    logger.info("\n" + "=" * 50)
    logger.info("₿ ESTRATEGIA 2: Crypto 15-Min (BTC/ETH/SOL)")
    logger.info("=" * 50)
    try:
        btc_trade = await btc_strategy.run_cycle()
        if btc_trade:
            status = btc_trade.get("status", "UNKNOWN")
            if status == "EXECUTED":
                logger.info(f"   ✅ Trade crypto ejecutado: ${btc_trade['amount']:.2f} {btc_trade.get('side', '')}")
                tracker.add_trade(
                    market_id=btc_trade.get("market_id", ""),
                    question=btc_trade.get("question", btc_trade.get("crypto", "")),
                    side=btc_trade.get("side", ""),
                    amount=btc_trade["amount"],
                    price=btc_trade.get("price", 0.50),
                    strategy="CRYPTO"
                )
                if telegram:
                    await telegram.send_trade_alert(
                        "CRYPTO", btc_trade.get("question", btc_trade.get("crypto", "")),
                        btc_trade.get("side", ""), btc_trade["amount"],
                        btc_trade.get("price", 0.50), btc_trade.get("edge", 0), "~15 min")
                    telegram.log_trade("CRYPTO", btc_trade.get("question", ""), btc_trade.get("side", ""), btc_trade["amount"])
                    logger.info(f"   ⏳ Resuelve en: ~15 min")
            elif status == "FAILED":
                logger.info(f"   ❌ Trade crypto falló: ${btc_trade['amount']:.2f}")
            else:
                logger.info(f"   ℹ️ Trade crypto: {status}")
    except Exception as e:
        logger.error(f"   Error en BTC 15m: {e}")

    # ===== ESTRATEGIA 3: NO Harvester (dinero casi seguro) =====
    logger.info("\n" + "=" * 50)
    logger.info("🌾 ESTRATEGIA 3: NO Harvester (>90% probabilidad)")
    logger.info("=" * 50)
    try:
        harvest_opps = await harvester.find_harvest_opportunities()
        if harvest_opps:
            logger.info(f"   Encontradas {len(harvest_opps)} oportunidades de harvest")
            harvest_results = await harvester.execute_harvest(harvest_opps, max_harvests=3)
            executed_h = [h for h in harvest_results if h.get("status") == "EXECUTED"]
            if executed_h:
                total_profit = sum(h["expected_profit"] for h in executed_h)
                logger.info(f"   ✅ {len(executed_h)} harvests ejecutados | Profit esperado: ~${total_profit:.2f}")
                for h in executed_h:
                    tracker.add_trade(
                        market_id=h.get("market_id", ""),
                        question=h.get("question", ""),
                        side=h.get("side", ""),
                        amount=h.get("bet_amount", 0),
                        price=h.get("price", 0.95),
                        strategy="HARVEST"
                    )
                    if telegram:
                        telegram.log_trade("HARVEST", h.get("question", ""), h.get("side", ""), h.get("bet_amount", 0))
        else:
            logger.info("   Sin oportunidades de harvest en este ciclo")
    except Exception as e:
        logger.error(f"   Error en Harvester: {e}")

    # ===== ESTRATEGIA 4: Weather Trader =====
    logger.info("\n" + "=" * 50)
    logger.info("⛅ ESTRATEGIA 4: Weather Trader (clima)")
    logger.info("=" * 50)
    try:
        weather_trade = await weather_trader.run_cycle()
        if weather_trade:
            status = weather_trade.get("status", "UNKNOWN")
            if status == "EXECUTED":
                logger.info(f"   ✅ Weather trade ejecutado: ${weather_trade['amount']:.2f} "
                           f"{weather_trade.get('side', '')} | Edge: {weather_trade.get('edge', 0):.1%}")
                tracker.add_trade(
                    market_id=weather_trade.get("market_id", ""),
                    question=weather_trade.get("question", ""),
                    side=weather_trade.get("side", ""),
                    amount=weather_trade["amount"],
                    price=weather_trade.get("price", 0.50),
                    strategy="WEATHER"
                )
                if telegram:
                    await telegram.send_trade_alert(
                        "WEATHER", weather_trade.get("question", ""),
                        weather_trade.get("side", ""), weather_trade["amount"],
                        weather_trade.get("price", 0.50), weather_trade.get("edge", 0), "hoy/mañana")
                    telegram.log_trade("WEATHER", weather_trade.get("question", ""), weather_trade.get("side", ""), weather_trade["amount"])
                    logger.info(f"   ⏳ Resuelve en: hoy/mañana")
            elif status == "SIMULATED":
                logger.info(f"   🏃 Weather simulado: ${weather_trade['amount']:.2f} {weather_trade.get('side', '')}")
            elif status == "FAILED":
                logger.info(f"   ❌ Weather trade falló")
            else:
                logger.info(f"   ℹ️ Weather: {status}")
    except Exception as e:
        logger.error(f"   Error en Weather Trader: {e}")

    # ===== ESTRATEGIA 5: Stock Market Trader =====
    logger.info("\n" + "=" * 50)
    logger.info("📈 ESTRATEGIA 5: Stock Market Trader (S&P/NASDAQ/Dow)")
    logger.info("=" * 50)
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
                    strategy="STOCKS"
                )
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
            else:
                logger.info(f"   ℹ️ Stocks: {status}")
    except Exception as e:
        logger.error(f"   Error en Stock Trader: {e}")


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
        except Exception as _e:
            logger.debug(f"Error actualizando P&L: {_e}")

    # ===== AUTO-COBRO (cada 4 ciclos ≈ 1 hora) =====
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
                redeem_result = await redeemer.run_cycle()
                if redeem_result.get("redeemed", 0) > 0:
                    logger.info(f"   💰 Cobradas: {redeem_result['redeemed']} | "
                                f"+${redeem_result['amount']:.2f}")
                    if telegram:
                        await telegram.send_redeem_alert(
                            redeem_result['amount'], redeem_result['redeemed'],
                            STATE.current_bankroll)
                    # Also unwrap any remaining WCOL
                    try:
                        from web3 import Web3 as _W3r
                        _pk_r = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
                        _w3r = _W3r(_W3r.HTTPProvider("https://polygon-bor-rpc.publicnode.com"))
                        _eoa_r = _w3r.eth.account.from_key(_pk_r).address
                        _wcol_abi = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
                                     {"inputs":[{"name":"_to","type":"address"},{"name":"_amount","type":"uint256"}],"name":"unwrap","outputs":[],"type":"function"}]
                        _wcol_c = _w3r.eth.contract(address=_w3r.to_checksum_address("0x3A3BD7bb9528E159577F7C2e685CC81A765002E2"), abi=_wcol_abi)
                        _wcol_bal = _wcol_c.functions.balanceOf(_eoa_r).call()
                        if _wcol_bal > 0:
                            _n = _w3r.eth.get_transaction_count(_eoa_r)
                            _tx = _wcol_c.functions.unwrap(_eoa_r, _wcol_bal).build_transaction({
                                'from': _eoa_r, 'nonce': _n, 'gas': 200000,
                                'gasPrice': _w3r.eth.gas_price, 'chainId': 137})
                            _s = _w3r.eth.account.sign_transaction(_tx, _pk_r)
                            _w3r.eth.send_raw_transaction(_s.raw_transaction)
                            logger.info(f"   Unwrapped {_wcol_bal/1e6:.2f} WCOL restante")
                    except Exception:
                        pass
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

            await telegram.send_periodic_report(
                bankroll=STATE.current_bankroll,
                pnl_total=STATE.total_pnl,
                positions=_positions_tg or [],
                tracker_summary=tracker.get_summary()
            )
        except Exception as _e_tg:
            logger.debug(f"Error reporte Telegram: {_e_tg}")


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
    btc_strategy = BTC15MinStrategy()

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
                logger.info(f"   💰 Balance real USDC.e: ${balance:.2f}")
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
    logger.info(f"     1. 🧠 IA Value Bets (Claude) - cada 15 min")
    logger.info(f"     2. ₿ Crypto 15-Min (BTC/ETH/SOL momentum) - cada 15 min")
    logger.info(f"     3. 🌾 NO Harvester (>90% probabilidad) - cada 15 min")
    logger.info(f"     4. ⛅ Weather Trader (Open-Meteo 6 modelos) - cada 15 min")
    logger.info(f"     5. 📈 Stock Trader (S&P/NASDAQ/Dow) - cada 3 min")

    # Inicializar componentes auxiliares
    redeemer = AutoRedeemer()
    harvester = NOHarvester()
    tracker = WinRateTracker()
    weather_trader = WeatherTrader()
    stock_trader = StockTrader()

    telegram = TelegramMonitor()

    # Enviar notificación de inicio
    if telegram.enabled:
        mode = "LIVE 💰" if not SAFETY.dry_run else "DRY RUN 🏃"
        await telegram.send_startup(STATE.current_bankroll, mode)

    try:
        if args.once or args.scan_only:
            # Un solo ciclo
            await run_cycle(scanner, analyzer, risk, executor,
                          btc_strategy, redeemer, harvester, tracker,
                          weather_trader, stock_trader, telegram, args.scan_only)
        else:
            # Loop continuo
            last_ia_scan = 0
            ia_interval = SAFETY.scan_interval_minutes * 60

            stop_hour = getattr(args, 'stop_at', None)
            if stop_hour:
                logger.info(f"⏰ Auto-stop programado a las {stop_hour}:00")

            logger.info("\n🚀 Modo continuo: 6 estrategias activas\n")

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
                    logger.info("🧠 Ciclo completo (cada 15 min) — 6 estrategias")
                    logger.info("=" * 50)
                    await run_cycle(scanner, analyzer, risk, executor,
                                  btc_strategy, redeemer, harvester, tracker,
                                  weather_trader, stock_trader, telegram)
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
        await btc_strategy.close()
        await redeemer.close()
        await weather_trader.close()
        await stock_trader.close()
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
