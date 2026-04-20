"""
PolyBot - Telegram Monitor
============================
Envía reportes periódicos a Telegram para monitorear el bot
desde el celular. Incluye:
- Balance libre / en posiciones / total estimado
- Posiciones clasificadas (GANANDO / EN JUEGO / PERDIENDO)
- Tiempo hasta resolución por posición
- Trades ejecutados con hora de apuesta y cuenta regresiva
- Alertas de errores y cobros

SETUP:
1. Habla con @BotFather en Telegram → /newbot → copia el token
2. Habla con @userinfobot → copia tu chat_id
3. Agrega a .env:
   TELEGRAM_BOT_TOKEN=tu_token
   TELEGRAM_CHAT_ID=tu_chat_id
"""

import os
import logging
import aiohttp
from datetime import datetime, timezone
from typing import Optional, Dict, List

logger = logging.getLogger("polybot.telegram")


class TelegramMonitor:
    """Envía reportes del bot a Telegram."""

    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        self.session: Optional[aiohttp.ClientSession] = None
        self.cycle_count = 0
        self.report_interval = 4  # Reportar cada 4 ciclos (~1 hora)
        self.last_trades: List[str] = []  # Trades del ciclo actual

        if self.enabled:
            logger.info(f"   📱 Telegram Monitor activo (chat_id={self.chat_id})")
        else:
            logger.info("   📱 Telegram no configurado (opcional)")

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Crea una sesión con SSL context correcto.
        En Windows, los certificados del sistema a veces tienen problemas
        con Python 3.12+ (Basic Constraints no marcado critical).
        Usa certifi si está disponible, sino desactiva verificación SSL
        (solo para api.telegram.org que es un servicio de confianza).
        """
        if self.session is None or self.session.closed:
            connector = None
            try:
                import ssl
                import certifi
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            except Exception:
                # Fallback: desactivar verificación SSL si certifi falla
                # Esto solo afecta llamadas al servidor de Telegram (api.telegram.org)
                connector = aiohttp.TCPConnector(ssl=False)

            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def send(self, message: str, parse_mode: Optional[str] = None):
        """
        Envía mensaje a Telegram con manejo de errores visible.
        Por defecto sin parse_mode (texto plano) para evitar errores
        de Markdown con caracteres especiales en nombres de mercados.
        """
        if not self.enabled:
            return False

        try:
            session = await self._get_session()
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message[:4000],  # Telegram max 4096 chars
                "disable_web_page_preview": True,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode

            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return True
                # Error visible — por esto no llegaba nada antes
                body = await resp.text()
                logger.warning(
                    f"Telegram HTTP {resp.status}: {body[:200]}"
                )
                return False
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")
            return False

    # =================================================================
    # HELPERS
    # =================================================================

    @staticmethod
    def _calc_resolve_time(end_date_str: str) -> str:
        """
        Retorna un tag corto con el tiempo hasta resolución.
        Ejemplos: [POR COBRAR], [45min], [3.2hrs], [2d]
        """
        if not end_date_str:
            return ""
        try:
            if end_date_str.endswith("Z"):
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            elif "+" in end_date_str[-6:]:
                end_dt = datetime.fromisoformat(end_date_str)
            else:
                end_dt = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
            diff = (end_dt - datetime.now(timezone.utc)).total_seconds()
            if diff <= 0:
                return "[POR COBRAR]"
            if diff < 3600:
                return f"[{diff/60:.0f}min]"
            if diff < 86400:
                return f"[{diff/3600:.1f}hrs]"
            return f"[{diff/86400:.0f}d]"
        except Exception:
            return ""

    # =================================================================
    # REPORTES
    # =================================================================

    async def send_startup(self, bankroll: float, mode: str):
        """Envía notificación de inicio."""
        msg = (
            f"🤖 PolyBot INICIADO\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: ${bankroll:.2f}\n"
            f"⚙️ Modo: {mode}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S %d/%m')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Reportes cada ~1 hora"
        )
        ok = await self.send(msg)
        if ok:
            logger.info("   Telegram: mensaje de inicio enviado")
        else:
            logger.warning("   Telegram: fallo al enviar mensaje de inicio")

    async def send_trade_alert(self, strategy: str, question: str,
                                side: str, amount: float, price: float,
                                edge: float, end_date: str = ""):
        """Alerta inmediata de trade ejecutado."""
        emoji = {"IA": "🧠", "CRYPTO": "₿", "HARVEST": "🌾",
                 "WEATHER": "⛅", "STOCKS": "📈", "FLASH_CRASH": "⚡",
                 "SPORTS": "⚽", "ESPORTS": "🎮"
                 }.get(strategy, "🎯")

        # Calcular info de resolución
        resolve_line = ""
        if end_date:
            try:
                if end_date.endswith("Z"):
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                elif "+" in end_date[-6:]:
                    end_dt = datetime.fromisoformat(end_date)
                else:
                    end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
                diff = (end_dt - datetime.now(timezone.utc)).total_seconds()
                if diff <= 0:
                    resolve_line = "🏁 Resuelve: ya por cobrar\n"
                elif diff < 3600:
                    resolve_line = f"🏁 Resuelve en: {diff/60:.0f} min\n"
                elif diff < 86400:
                    local_hm = end_dt.astimezone().strftime("%H:%M")
                    resolve_line = f"🏁 Resuelve en: {diff/3600:.1f}h (aprox {local_hm})\n"
                else:
                    local_dm = end_dt.astimezone().strftime("%d/%m %H:%M")
                    resolve_line = f"🏁 Resuelve en: {diff/86400:.1f}d (aprox {local_dm})\n"
            except Exception:
                resolve_line = ""

        msg = (
            f"{emoji} TRADE EJECUTADO\n"
            f"📋 {question[:60]}\n"
            f"📍 {side} ${amount:.2f} @ {price:.2f}\n"
            f"📊 Edge: {edge:.1%}\n"
            f"{resolve_line}"
            f"📤 Apostada: {datetime.now().strftime('%H:%M')}"
        )
        await self.send(msg)

    async def send_periodic_report(self, bankroll: float, pnl_total: float,
                                     positions: List[Dict],
                                     tracker_summary: str,
                                     stock_daily_count: Optional[int] = None,
                                     stock_daily_limit: int = 4):
        """
        Reporte periódico completo (~cada hora).
        Formato rico con Balance libre / En posiciones / Total estimado
        y clasificación de posiciones en GANANDO / EN JUEGO / PERDIENDO.
        """
        self.cycle_count += 1
        if self.cycle_count < self.report_interval:
            return
        self.cycle_count = 0

        # Filtrar posiciones activas (valor > 1¢) y calcular totales.
        # Calculamos value = size * curPrice en vez de confiar en
        # currentValue del API. Razón: vimos casos (MSFT 15-Abr) en
        # que currentValue mostraba ~$21 mientras Polymarket UI decía
        # $0.12. La fórmula size*curPrice replica lo que Polymarket
        # muestra en su frontend y elimina dependencia de cualquier
        # campo cacheado/derivado del API.
        active = []
        total_val = 0.0
        if positions:
            for p in positions:
                size = float(p.get("size") or 0)
                cur_price = float(p.get("curPrice") or 0)
                val = size * cur_price
                if val > 0.01:
                    p["_value"] = val  # cache para reusar abajo
                    active.append(p)
                    total_val += val

        total_estimated = bankroll + total_val
        # Asumimos $200 como capital inicial para ROI (ajustable)
        initial = 200.0
        pnl_real = total_estimated - initial
        real_roi = (pnl_real / initial) * 100 if initial > 0 else 0
        pnl_emoji = "📈" if pnl_real >= 0 else "📉"

        lines = [
            "📊 REPORTE POLYBOT",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 Balance libre: ${bankroll:.2f}",
            f"💰 En posiciones: ${total_val:.2f}",
            f"💰 Total estimado: ${total_estimated:.2f}",
            f"{pnl_emoji} P/L: ${pnl_real:+.2f} ({real_roi:+.1f}%)",
            f"🕐 {datetime.now().strftime('%H:%M %d/%m')}",
        ]

        if stock_daily_count is not None:
            remaining = max(0, stock_daily_limit - stock_daily_count)
            lines.append(
                f"📊 STOCKS: {stock_daily_count}/{stock_daily_limit} "
                f"bets hoy ({remaining} disponibles)"
            )

        if active:
            # Clasificar posiciones por curPrice
            winning = []   # cur_price >= 0.85 (casi ganadas)
            losing = []    # cur_price < 0.30 (casi perdidas)
            pending = []   # el resto (en juego)
            for p in active:
                cur_price = float(p.get("curPrice") or 0)
                if cur_price >= 0.85:
                    winning.append(p)
                elif cur_price < 0.30:
                    losing.append(p)
                else:
                    pending.append(p)

            # GANADORAS
            if winning:
                lines.append(f"\n✅ GANANDO ({len(winning)}):")
                for p in sorted(winning, key=lambda x: x.get("_value", 0), reverse=True)[:8]:
                    title = (p.get("title") or p.get("question") or "?")[:28]
                    value = p.get("_value", 0)
                    cur_price = float(p.get("curPrice") or 0)
                    side = p.get("outcome") or "?"
                    resolve = self._calc_resolve_time(p.get("endDate") or "")
                    lines.append(f"  {title} | {side} ${value:.2f} ({cur_price:.0%}) {resolve}")
                if len(winning) > 8:
                    lines.append(f"  ... y {len(winning)-8} más")

            # EN JUEGO
            if pending:
                lines.append(f"\n⏳ EN JUEGO ({len(pending)}):")
                for p in sorted(pending, key=lambda x: x.get("_value", 0), reverse=True)[:6]:
                    title = (p.get("title") or p.get("question") or "?")[:28]
                    value = p.get("_value", 0)
                    cur_price = float(p.get("curPrice") or 0)
                    side = p.get("outcome") or "?"
                    pnl = float(p.get("cashPnl") or 0)
                    resolve = self._calc_resolve_time(p.get("endDate") or "")
                    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                    lines.append(f"  {title} | {side} ${value:.2f} ({cur_price:.0%}) {pnl_str} {resolve}")
                if len(pending) > 6:
                    lines.append(f"  ... y {len(pending)-6} más")

            # PERDIENDO — mostrar curPrice + cashPnl recalculado para
            # evitar discrepancias con Polymarket UI (bug MSFT 15-Abr).
            if losing:
                lines.append(f"\n❌ PERDIENDO ({len(losing)}):")
                for p in sorted(losing, key=lambda x: x.get("_value", 0), reverse=True)[:4]:
                    title = (p.get("title") or p.get("question") or "?")[:28]
                    value = p.get("_value", 0)
                    cur_price = float(p.get("curPrice") or 0)
                    initial = float(p.get("initialValue") or 0)
                    # Recalcular pnl desde value canónico (size*curPrice)
                    # en lugar de confiar en cashPnl del API
                    pnl = value - initial
                    side = p.get("outcome") or "?"
                    pnl_str = f"-${abs(pnl):.2f}" if pnl < 0 else f"+${pnl:.2f}"
                    lines.append(f"  {title} | {side} ${value:.2f} ({cur_price:.0%}) {pnl_str}")

        # Historial completo del tracker (overall + por estrategia).
        # Antes solo se mostraba la primera línea, ocultando el
        # desglose SPORTS/STOCKS/CRYPTO que es información clave.
        lines.append("\n📊 HISTORIAL:")
        if tracker_summary:
            for sl in tracker_summary.split("\n"):
                sl = sl.strip()
                if sl:
                    lines.append(f"  {sl[:140]}")

        # Trades recientes del ciclo
        if self.last_trades:
            lines.append("\n🔄 Trades recientes:")
            for t in self.last_trades[-5:]:
                lines.append(f"  {t}")
            self.last_trades.clear()

        await self.send("\n".join(lines))

    async def send_redeem_alert(self, amount: float, count: int,
                                  new_balance: float,
                                  markets: Optional[List[str]] = None):
        """
        Alerta cuando se cobran posiciones resueltas.
        Si se proveen `markets` (lista de strings con títulos), se
        incluyen en el mensaje para que el usuario sepa QUÉ se cobró.
        """
        lines = [
            "💰 COBRO EXITOSO",
            "━━━━━━━━━━━━━━━━━━",
            f"Cobradas: {count} posiciones",
            f"Recibido: +${amount:.2f}",
            f"Balance: ${new_balance:.2f}",
        ]

        if markets:
            lines.append("")
            lines.append("📋 Mercados cobrados:")
            # Mostrar hasta 8 mercados (para no exceder límite Telegram)
            for m in markets[:8]:
                lines.append(f"  • {m}")
            if len(markets) > 8:
                lines.append(f"  ... y {len(markets) - 8} más")

        await self.send("\n".join(lines))

    async def send_error_alert(self, error: str):
        """Alerta de error crítico."""
        clean = str(error)[:200].replace("_", " ")
        msg = (
            f"🚨 ERROR POLYBOT\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{clean}\n"
            f"⏰ {datetime.now().strftime('%H:%M')}"
        )
        await self.send(msg)

    async def send_shutdown(self, bankroll: float, total_trades: int,
                              tracker_summary: str):
        """Notificación de apagado."""
        clean_summary = (tracker_summary or "")[:200]
        msg = (
            f"🛑 POLYBOT DETENIDO\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: ${bankroll:.2f}\n"
            f"📊 Trades hoy: {total_trades}\n"
            f"{clean_summary}\n"
            f"⏰ {datetime.now().strftime('%H:%M')}"
        )
        await self.send(msg)

    def log_trade(self, strategy: str, question: str, side: str,
                  amount: float):
        """Registra trade para incluir en próximo reporte periódico."""
        short_q = question[:30]
        self.last_trades.append(
            f"{strategy}: {side} ${amount:.2f} | {short_q}"
        )
