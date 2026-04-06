"""
PolyBot - Telegram Monitor (v2 - plain text, no Markdown issues)
==================================================================
Envía alertas a Telegram. Usa texto plano para evitar errores
de formato con caracteres especiales.

SETUP:
1. @BotFather → /newbot → copia el token
2. @userinfobot → copia tu chat_id
3. En .env:
   TELEGRAM_BOT_TOKEN=tu_token
   TELEGRAM_CHAT_ID=tu_chat_id
"""

import os
import ssl
import logging
import aiohttp
from datetime import datetime, timezone
from typing import Optional, Dict, List

logger = logging.getLogger("polybot.telegram")


class TelegramMonitor:
    """Envía reportes del bot a Telegram (texto plano, sin Markdown)."""

    def __init__(self):
        from dotenv import load_dotenv
        load_dotenv()  # Re-cargar .env por si se agregó después
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.bot_token and self.chat_id)
        self.session: Optional[aiohttp.ClientSession] = None
        self.cycle_count = 0
        self.report_interval = 4  # Reportar cada 4 ciclos (~1 hora)
        self.last_trades: List[str] = []

        if self.enabled:
            logger.info(f"   Telegram Monitor activo (chat_id={self.chat_id})")
        else:
            logger.info(f"   Telegram no configurado (token={'si' if self.bot_token else 'no'}, chat={'si' if self.chat_id else 'no'})")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            # Desactivar verificación SSL (fix para Windows con certs desactualizados)
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                connector=connector
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def send(self, message: str):
        """Envía mensaje a Telegram (texto plano, sin parse_mode)."""
        if not self.enabled:
            return False

        try:
            session = await self._get_session()
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            async with session.post(url, json={
                "chat_id": self.chat_id,
                "text": message[:4000],  # Telegram max 4096 chars
                "disable_web_page_preview": True,
            }) as resp:
                result = await resp.json()
                if resp.status == 200 and result.get("ok"):
                    return True
                else:
                    logger.warning(f"Telegram error: {result.get('description', resp.status)}")
                    return False
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════
    # ALERTAS
    # ═══════════════════════════════════════════════════════════════

    async def send_startup(self, bankroll: float, mode: str):
        """Notificación de inicio."""
        msg = (
            f"🤖 POLYBOT INICIADO\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: ${bankroll:.2f}\n"
            f"⚙️ Modo: {mode}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Alertas de trades activas\n"
            f"Reporte cada ~1 hora"
        )
        sent = await self.send(msg)
        if sent:
            logger.info("   Telegram: mensaje de inicio enviado")
        else:
            logger.warning("   Telegram: fallo al enviar mensaje de inicio")

    async def send_trade_alert(self, strategy: str, question: str,
                                side: str, amount: float, price: float,
                                edge: float, resolve_time: str = ""):
        """Alerta de trade ejecutado."""
        emoji = {"IA": "🧠", "CRYPTO": "₿", "HARVEST": "🌾",
                 "WEATHER": "⛅", "STOCKS": "📈"}.get(strategy, "🎯")

        resolve_str = f"\n⏳ Resuelve en: {resolve_time}" if resolve_time else ""
        edge_pct = f"{edge*100:.1f}%"

        msg = (
            f"{emoji} TRADE EJECUTADO\n"
            f"📋 {question[:50]}\n"
            f"📍 {side} ${amount:.2f} @ ${price:.2f}\n"
            f"📊 Edge: {edge_pct}{resolve_str}\n"
            f"⏰ {datetime.now().strftime('%H:%M')}"
        )
        await self.send(msg)

    async def send_periodic_report(self, bankroll: float, pnl_total: float,
                                     positions: List[Dict],
                                     tracker_summary: str):
        """Reporte periódico (~cada hora) con posiciones detalladas."""
        self.cycle_count += 1
        # Primer reporte inmediato, después cada 4 ciclos
        if self.cycle_count > 1 and self.cycle_count < self.report_interval:
            return
        if self.cycle_count >= self.report_interval:
            self.cycle_count = 0

        pnl_emoji = "📈" if pnl_total >= 0 else "📉"
        roi = ((bankroll - 200) / 200) * 100

        lines = [
            f"📊 REPORTE POLYBOT",
            f"━━━━━━━━━━━━━━━━━━",
            f"💰 Balance libre: ${bankroll:.2f}",
        ]

        # Posiciones detalladas con tiempo de resolución
        if positions:
            active = [p for p in positions if float(p.get("currentValue") or 0) > 0.01]
            total_val = sum(float(p.get("currentValue") or 0) for p in active)
            total_pnl_pos = sum(float(p.get("cashPnl") or 0) for p in active)

            # Clasificar
            winning = []
            losing = []
            pending = []
            for p in active:
                cur_price = float(p.get("curPrice") or 0)
                pnl = float(p.get("cashPnl") or 0)
                if cur_price >= 0.85:
                    winning.append(p)
                elif cur_price < 0.30:
                    losing.append(p)
                else:
                    pending.append(p)

            lines.append(f"💰 En posiciones: ${total_val:.2f}")
            lines.append(f"💰 Total estimado: ${bankroll + total_val:.2f}")
            lines.append(f"{pnl_emoji} P/L posiciones: ${total_pnl_pos:+.2f}")
            lines.append(f"📈 ROI: {roi:+.1f}%")
            lines.append(f"🕐 {datetime.now().strftime('%H:%M %d/%m')}")

            # GANADORAS
            if winning:
                lines.append(f"\n✅ GANANDO ({len(winning)}):")
                for p in sorted(winning, key=lambda x: float(x.get("currentValue") or 0), reverse=True)[:8]:
                    title = (p.get("title") or p.get("question") or "?")[:28]
                    value = float(p.get("currentValue") or 0)
                    cur_price = float(p.get("curPrice") or 0)
                    side = p.get("outcome") or "?"
                    resolve = self._calc_resolve_time(p.get("endDate") or "")
                    lines.append(f"  {title} | {side} ${value:.2f} ({cur_price:.0%}) {resolve}")
                if len(winning) > 8:
                    lines.append(f"  ... y {len(winning)-8} mas")

            # EN JUEGO
            if pending:
                lines.append(f"\n⏳ EN JUEGO ({len(pending)}):")
                for p in sorted(pending, key=lambda x: float(x.get("currentValue") or 0), reverse=True)[:6]:
                    title = (p.get("title") or p.get("question") or "?")[:28]
                    value = float(p.get("currentValue") or 0)
                    cur_price = float(p.get("curPrice") or 0)
                    side = p.get("outcome") or "?"
                    pnl = float(p.get("cashPnl") or 0)
                    resolve = self._calc_resolve_time(p.get("endDate") or "")
                    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                    lines.append(f"  {title} | {side} ${value:.2f} ({cur_price:.0%}) {pnl_str} {resolve}")
                if len(pending) > 6:
                    lines.append(f"  ... y {len(pending)-6} mas")

            # PERDIENDO
            if losing:
                lines.append(f"\n❌ PERDIENDO ({len(losing)}):")
                for p in losing[:4]:
                    title = (p.get("title") or p.get("question") or "?")[:28]
                    value = float(p.get("currentValue") or 0)
                    pnl = float(p.get("cashPnl") or 0)
                    lines.append(f"  {title} | ${value:.2f} (P/L: -${abs(pnl):.2f})")
        else:
            lines.append(f"💰 Total: ${bankroll:.2f}")
            lines.append(f"📈 ROI: {roi:+.1f}%")

        # Win rate resumen
        lines.append(f"\n📊 HISTORIAL:")
        # Extraer solo las líneas principales del tracker summary
        summary_lines = tracker_summary.split("\n")
        if summary_lines:
            lines.append(f"  {summary_lines[0][:100]}")

        # Trades recientes
        if self.last_trades:
            lines.append(f"\n🔄 Trades recientes:")
            for t in self.last_trades[-5:]:
                lines.append(f"  {t}")
            self.last_trades.clear()

        await self.send("\n".join(lines))

    async def send_redeem_alert(self, amount: float, count: int,
                                  new_balance: float):
        """Alerta de cobro exitoso."""
        msg = (
            f"💰 COBRO EXITOSO\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Cobradas: {count} posiciones\n"
            f"Ganancia: +${amount:.2f}\n"
            f"Balance: ${new_balance:.2f}"
        )
        await self.send(msg)

    async def send_error_alert(self, error: str):
        """Alerta de error."""
        clean_error = str(error)[:200].replace("_", " ")
        msg = (
            f"🚨 ERROR POLYBOT\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{clean_error}\n"
            f"⏰ {datetime.now().strftime('%H:%M')}"
        )
        await self.send(msg)

    async def send_shutdown(self, bankroll: float, total_trades: int,
                              tracker_summary: str):
        """Notificación de apagado."""
        clean_summary = tracker_summary[:200].replace("$+", "$")
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
        """Registra trade para reporte periódico."""
        short_q = question[:30]
        self.last_trades.append(
            f"{strategy}: {side} ${amount:.2f} | {short_q}"
        )

    @staticmethod
    def _calc_resolve_time(end_date_str: str) -> str:
        """Calcula tiempo hasta resolución."""
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
                return "[RESUELTO]"
            if diff < 3600:
                return f"[{diff/60:.0f}min]"
            if diff < 86400:
                return f"[{diff/3600:.1f}hrs]"
            return f"[{diff/86400:.0f}d]"
        except:
            return ""
