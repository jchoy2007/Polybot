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
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
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
        """Reporte periódico (~cada hora)."""
        self.cycle_count += 1
        if self.cycle_count < self.report_interval:
            return
        self.cycle_count = 0

        pnl_emoji = "📈" if pnl_total >= 0 else "📉"
        roi = ((bankroll - 200) / 200) * 100

        lines = [
            f"📊 REPORTE POLYBOT",
            f"━━━━━━━━━━━━━━━━━━",
            f"💰 Balance libre: ${bankroll:.2f}",
            f"{pnl_emoji} P/L Total: ${pnl_total:+.2f}",
            f"📈 ROI: {roi:+.1f}%",
            f"🕐 {datetime.now().strftime('%H:%M %d/%m')}",
        ]

        # Posiciones con horas hasta resolución
        if positions:
            active = [p for p in positions if float(p.get("currentValue") or 0) > 0.01]
            total_val = sum(float(p.get("currentValue") or 0) for p in active)
            winning = sum(1 for p in active if float(p.get("curPrice") or 0) >= 0.70)
            losing = sum(1 for p in active if float(p.get("curPrice") or 0) < 0.30)

            lines.append(f"\n📋 Posiciones: {len(active)}")
            lines.append(f"💰 Valor total: ${total_val:.2f}")
            lines.append(f"✅ Ganando: {winning} | ❌ Perdiendo: {losing}")

            # Top 5 posiciones por valor
            sorted_pos = sorted(active,
                                key=lambda p: float(p.get("currentValue") or 0),
                                reverse=True)
            for p in sorted_pos[:6]:
                title = (p.get("title") or p.get("question") or "?")[:30]
                value = float(p.get("currentValue") or 0)
                cur_price = float(p.get("curPrice") or 0)
                side = p.get("outcome") or "?"
                # Tiempo de resolución
                end_date = p.get("endDate") or ""
                resolve = self._calc_resolve_time(end_date)
                emoji = "✅" if cur_price >= 0.70 else ("❌" if cur_price < 0.30 else "⏳")
                lines.append(f"{emoji} {title} | {side} ${value:.2f} ({cur_price:.0%}) {resolve}")

            if len(sorted_pos) > 6:
                lines.append(f"   ... y {len(sorted_pos)-6} mas")

        # Win rate (limpiar caracteres problemáticos)
        clean_summary = tracker_summary.replace("$+", "$").replace("+$", "+$")
        lines.append(f"\n{clean_summary[:250]}")

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
