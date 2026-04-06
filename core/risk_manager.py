"""
PolyBot - Motor de Gestión de Riesgo
=====================================
Implementa el Criterio de Kelly fraccional y todas las
reglas de seguridad para proteger tu capital.
"""

import math
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from config.settings import SAFETY, STATE

logger = logging.getLogger("polybot.risk")


class RiskManager:
    """Gestiona el riesgo de cada apuesta y del portafolio completo."""

    def __init__(self):
        self.trade_log = []
        self.daily_trades = []

    # =================================================================
    # CRITERIO DE KELLY
    # =================================================================

    def kelly_criterion(self, prob_win: float, odds: float) -> float:
        """
        Calcula el tamaño óptimo de apuesta usando el Criterio de Kelly.

        En Polymarket:
        - prob_win: tu probabilidad estimada de que el resultado sea 'Yes'
        - odds: lo que pagas por el share (ej: 0.52 = pagas $0.52, ganas $1)

        Retorna: fracción del bankroll a apostar (0 a 1)
        """
        if prob_win <= 0 or prob_win >= 1:
            return 0.0
        if odds <= 0 or odds >= 1:
            return 0.0

        # En Polymarket: compras a precio `odds`, si ganas recibes $1
        # Ganancia neta si ganas: (1 - odds) / odds
        # Pérdida si pierdes: -1 (pierdes lo apostado)
        b = (1 - odds) / odds  # ratio ganancia/pérdida
        q = 1 - prob_win

        # Kelly: f* = (b*p - q) / b
        kelly = (b * prob_win - q) / b

        if kelly <= 0:
            return 0.0  # No hay edge, no apostar

        # Aplicar fracción de Kelly (más conservador)
        fractional_kelly = kelly * SAFETY.kelly_fraction

        return max(0, min(fractional_kelly, 1.0))

    # =================================================================
    # CÁLCULO DE EDGE
    # =================================================================

    def calculate_edge(self, estimated_prob: float, market_price: float) -> float:
        """
        Calcula el edge (ventaja) entre tu estimación y el precio del mercado.

        Si estimated_prob = 0.65 y market_price = 0.52:
        Edge = 0.65 - 0.52 = 0.13 (13%)
        """
        return estimated_prob - market_price

    # =================================================================
    # ¿DEBO APOSTAR?
    # =================================================================

    def should_bet(self, estimated_prob: float, market_price: float,
                   market_liquidity: float, market_volume: float,
                   category: str) -> Tuple[bool, str, float]:
        """
        Decide si el bot debe apostar y cuánto.

        Retorna: (debe_apostar, razón, monto_usd)
        """
        # --- Verificación 1: ¿Bot está pausado? ---
        if STATE.is_paused:
            return False, f"Bot pausado: {STATE.pause_reason}", 0.0

        # --- Verificación 1.5: ¿Hay capital suficiente? ---
        if STATE.current_bankroll < 1.0:
            return False, f"Capital insuficiente: ${STATE.current_bankroll:.2f}", 0.0

        # --- Verificación 2: Stop-loss diario ---
        if STATE.daily_pnl <= -(STATE.current_bankroll * SAFETY.max_daily_loss_pct):
            self._activate_stoploss("Stop-loss diario activado")
            return False, "Stop-loss diario alcanzado", 0.0

        # --- Verificación 3: Stop-loss semanal ---
        if STATE.weekly_pnl <= -(STATE.current_bankroll * SAFETY.max_weekly_loss_pct):
            self._activate_stoploss("Stop-loss semanal activado")
            return False, "Stop-loss semanal alcanzado", 0.0

        # --- Verificación 4: Stop-loss total ---
        if STATE.total_pnl <= -(SAFETY.initial_bankroll * SAFETY.max_total_loss_pct):
            self._activate_stoploss("Stop-loss total activado")
            return False, "Stop-loss total alcanzado (-20%)", 0.0

        # --- Verificación 5: Posiciones abiertas ---
        if STATE.open_positions >= SAFETY.max_open_positions:
            return False, f"Máximo de {SAFETY.max_open_positions} posiciones alcanzado", 0.0

        # --- Verificación 6: Liquidez del mercado ---
        if market_liquidity < SAFETY.min_market_liquidity:
            return False, f"Liquidez insuficiente: ${market_liquidity:,.0f} < ${SAFETY.min_market_liquidity:,.0f}", 0.0

        # --- Verificación 7: Volumen del mercado ---
        if market_volume < SAFETY.min_market_volume:
            return False, f"Volumen insuficiente: ${market_volume:,.0f}", 0.0

        # --- Verificación 8: Categoría --- (DESACTIVADO - la IA ya filtra)
        # Todas las categorías son válidas

        # --- Verificación 9: Edge mínimo ---
        edge = self.calculate_edge(estimated_prob, market_price)
        if edge < SAFETY.min_edge_required:
            return False, f"Edge insuficiente: {edge:.1%} < {SAFETY.min_edge_required:.1%}", 0.0

        # --- Verificación 9.5: Expected Value mínimo ---
        # EV = P_true × (1 - P_market) - (1 - P_true) × P_market
        # Si EV < 5% → SKIP. Sin excepciones. (fuente: análisis 14K wallets)
        ev = estimated_prob * (1 - market_price) - (1 - estimated_prob) * market_price
        if ev < 0.05:
            return False, f"EV insuficiente: ${ev:.3f}/dólar < $0.05", 0.0

        # --- Verificación 10: Probabilidad razonable ---
        if estimated_prob < 0.10 or estimated_prob > 0.95:
            return False, f"Probabilidad extrema ({estimated_prob:.1%}), riesgo alto", 0.0

        # --- Verificación 11: Probabilidad mínima de ganar ---
        if estimated_prob < SAFETY.min_win_probability:
            return False, f"Prob de ganar muy baja ({estimated_prob:.0%} < {SAFETY.min_win_probability:.0%})", 0.0

        # --- Calcular tamaño de apuesta con Kelly + límites de settings ---
        kelly_pct = self.kelly_criterion(estimated_prob, market_price)
        bet_amount = STATE.current_bankroll * kelly_pct

        bankroll = STATE.current_bankroll

        # Aplicar límites de settings (respeta max_bet_absolute ya auto-escalado)
        bet_amount = max(bet_amount, SAFETY.min_bet_size)
        bet_amount = min(bet_amount, SAFETY.max_bet_absolute)
        bet_amount = min(bet_amount, bankroll * SAFETY.max_bet_pct)

        # No apostar más de lo que tenemos
        bet_amount = min(bet_amount, STATE.current_bankroll * 0.95)

        if bet_amount < SAFETY.min_bet_size:
            return False, "Monto calculado menor al mínimo", 0.0

        reason = (
            f"APOSTAR ${bet_amount:.2f} | "
            f"Edge: {edge:.1%} | "
            f"Kelly: {kelly_pct:.1%} | "
            f"Prob estimada: {estimated_prob:.1%} vs mercado: {market_price:.1%}"
        )

        return True, reason, round(bet_amount, 2)

    # =================================================================
    # GESTIÓN DE STOP-LOSS
    # =================================================================

    def _activate_stoploss(self, reason: str):
        """Activa el stop-loss y pausa el bot."""
        STATE.is_paused = True
        STATE.pause_reason = reason
        self._stoploss_activated_at = datetime.now()
        logger.warning(f"⚠️ STOP-LOSS ACTIVADO: {reason}")
        logger.warning(f"   Bot pausado por {SAFETY.cooldown_hours_after_stoploss} horas")

    def check_cooldown_expired(self) -> bool:
        """Verifica si el período de cooldown terminó."""
        if not hasattr(self, '_stoploss_activated_at'):
            return True  # No hay registro de cuándo se activó, permitir continuar
        elapsed = (datetime.now() - self._stoploss_activated_at).total_seconds() / 3600
        if elapsed >= SAFETY.cooldown_hours_after_stoploss:
            logger.info(f"   ✅ Cooldown expirado ({elapsed:.1f}h >= {SAFETY.cooldown_hours_after_stoploss}h)")
            return True
        logger.info(f"   ⏳ Cooldown: {elapsed:.1f}h / {SAFETY.cooldown_hours_after_stoploss}h")
        return False

    # =================================================================
    # REGISTRO DE TRADES
    # =================================================================

    def record_trade(self, market_id: str, market_question: str,
                     side: str, amount: float, price: float,
                     estimated_prob: float, edge: float,
                     result: Optional[str] = None):
        """Registra un trade en el log."""
        trade = {
            "timestamp": datetime.now().isoformat(),
            "market_id": market_id,
            "question": market_question,
            "side": side,
            "amount": amount,
            "price": price,
            "estimated_prob": estimated_prob,
            "edge": edge,
            "result": result,
            "bankroll_after": STATE.current_bankroll
        }
        self.trade_log.append(trade)
        self.daily_trades.append(trade)

        if SAFETY.log_every_decision:
            logger.info(f"📝 Trade: {json.dumps(trade, indent=2)}")

    def get_daily_summary(self) -> dict:
        """Genera resumen diario de operaciones."""
        return {
            "fecha": datetime.now().strftime("%Y-%m-%d"),
            "bankroll_actual": STATE.current_bankroll,
            "pnl_diario": STATE.daily_pnl,
            "pnl_total": STATE.total_pnl,
            "roi": f"{STATE.roi:.1f}%",
            "trades_hoy": len(self.daily_trades),
            "win_rate": f"{STATE.win_rate:.1%}",
            "posiciones_abiertas": STATE.open_positions,
            "bot_pausado": STATE.is_paused
        }
