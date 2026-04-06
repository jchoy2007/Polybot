"""
PolyBot - Telegram Monitor
============================
Envía reportes periódicos a Telegram para monitorear el bot
desde el celular. Incluye:
- Balance actual y P&L
- Posiciones activas con precios
- Trades ejecutados en el ciclo
- Alertas de errores

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
from datetime import datetime
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
            logger.info("   📱 Telegram Monitor activo")
        else:
            logger.info("   📱 Telegram no configurado (opcional)")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def send(self, message: str):
        """Envía mensaje a Telegram."""
        if not self.enabled:
            return

        try:
            session = await self._get_session()
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            await session.post(url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
        except Exception as e:
            logger.debug(f"Telegram send error: {e}")

    # ═══════════════════════════════════════════════════════════════
    # REPORTES
    # ═══════════════════════════════════════════════════════════════

    async def send_startup(self, bankroll: float, mode: str):
        """Envía notificación de inicio."""
        msg = (
            f"🤖 *PolyBot INICIADO*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: ${bankroll:.2f}\n"
            f"⚙️ Modo: {mode}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Reportes cada ~1 hora"
        )
        await self.send(msg)

    async def send_trade_alert(self, strategy: str, question: str,
                                side: str, amount: float, price: float,
                                edge: float):
        """Alerta inmediata de trade ejecutado."""
        emoji = {"IA": "🧠", "CRYPTO": "₿", "HARVEST": "🌾",
                 "WEATHER": "⛅", "STOCKS": "📈", "FLASH_CRASH": "⚡"
                 }.get(strategy, "🎯")

        msg = (
            f"{emoji} *TRADE EJECUTADO*\n"
            f"📋 {question[:50]}\n"
            f"📍 {side} ${amount:.2f} @ {price:.2f}\n"
            f"📊 Edge: {edge:.1%}\n"
            f"⏰ {datetime.now().strftime('%H:%M')}"
        )
        await self.send(msg)

    async def send_periodic_report(self, bankroll: float, pnl_total: float,
                                     positions: List[Dict],
                                     tracker_summary: str):
        """Reporte periódico completo (~cada hora)."""
        self.cycle_count += 1
        if self.cycle_count < self.report_interval:
            return
        self.cycle_count = 0

        # Header
        pnl_emoji = "📈" if pnl_total >= 0 else "📉"
        roi = ((bankroll - 200) / 200) * 100  # 200 = deposited

        lines = [
            f"📊 *REPORTE POLYBOT*",
            f"━━━━━━━━━━━━━━━━━━",
            f"💰 Balance: ${bankroll:.2f}",
            f"{pnl_emoji} P&L Total: ${pnl_total:+.2f}",
            f"📈 ROI: {roi:+.1f}%",
            f"🕐 {datetime.now().strftime('%H:%M %d/%m')}",
        ]

        # Posiciones activas (top 5)
        if positions:
            lines.append(f"\n📋 *Posiciones* ({len(positions)}):")
            # Sort by value descending
            sorted_pos = sorted(positions,
                                key=lambda p: float(p.get("currentValue") or 0),
                                reverse=True)
            for p in sorted_pos[:8]:
                title = (p.get("title") or p.get("question") or "?")[:35]
                value = float(p.get("currentValue") or 0)
                cur_price = float(p.get("curPrice") or 0)
                side = p.get("outcome") or "?"
                pnl = float(p.get("cashPnl") or 0)
                emoji = "✅" if pnl >= 0 else "📉"
                lines.append(f"{emoji} {title} | {side} ${value:.2f} ({cur_price:.0%})")

            if len(sorted_pos) > 8:
                lines.append(f"   ... +{len(sorted_pos)-8} más")

        # Win rate
        lines.append(f"\n{tracker_summary[:200]}")

        # Recent trades
        if self.last_trades:
            lines.append(f"\n🔄 *Trades recientes:*")
            for t in self.last_trades[-5:]:
                lines.append(f"  {t}")
            self.last_trades.clear()

        await self.send("\n".join(lines))

    async def send_redeem_alert(self, amount: float, count: int,
                                  new_balance: float):
        """Alerta cuando se cobran posiciones."""
        msg = (
            f"💰 *COBRO EXITOSO*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Cobradas: {count} posiciones\n"
            f"Ganancia: +${amount:.2f}\n"
            f"Balance: ${new_balance:.2f}"
        )
        await self.send(msg)

    async def send_error_alert(self, error: str):
        """Alerta de error crítico."""
        msg = (
            f"🚨 *ERROR POLYBOT*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{error[:200]}\n"
            f"⏰ {datetime.now().strftime('%H:%M')}"
        )
        await self.send(msg)

    async def send_shutdown(self, bankroll: float, total_trades: int,
                              tracker_summary: str):
        """Notificación de apagado."""
        msg = (
            f"🛑 *PolyBot DETENIDO*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance final: ${bankroll:.2f}\n"
            f"📊 Trades hoy: {total_trades}\n"
            f"{tracker_summary[:200]}\n"
            f"⏰ {datetime.now().strftime('%H:%M')}"
        )
        await self.send(msg)

    def log_trade(self, strategy: str, question: str, side: str,
                  amount: float):
        """Registra trade para incluir en reporte periódico."""
        short_q = question[:30]
        self.last_trades.append(
            f"{strategy}: {side} ${amount:.2f} | {short_q}"
        )
