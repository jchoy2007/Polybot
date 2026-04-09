"""
PolyBot - Configuración y Reglas de Seguridad
==============================================
ESTAS REGLAS SON INQUEBRANTABLES POR CÓDIGO.
El bot NUNCA podrá exceder estos límites.
"""

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

# Cargar .env ANTES de leer las variables
load_dotenv()

# ============================================================
# CONFIGURACIÓN DE APIs (edita tu archivo .env con tus claves)
# ============================================================
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_SECRET = os.getenv("POLYMARKET_SECRET", "")
POLYMARKET_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")
POLYGON_WALLET_PRIVATE_KEY = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# URLs DE LAS APIs
# ============================================================
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"

# ============================================================
# REGLAS DE SEGURIDAD (NO MODIFICAR SIN ENTENDER LOS RIESGOS)
# ============================================================

@dataclass
class SafetyRules:
    """Reglas de seguridad inquebrantables del bot."""

    # --- Capital inicial ---
    initial_bankroll: float = 200.0  # Dólares USDC (depositado real)

    # --- Límites por apuesta (QUARTER KELLY + AUTO-SCALING) ---
    min_bet_size: float = 1.50        # Mínimo por apuesta en USD (permite operar con capital bajo)
    max_bet_pct: float = 0.08         # Máximo 8% del bankroll por apuesta
    max_bet_absolute: float = 6.0     # Máximo por apuesta (auto-escala con capital)
    kelly_fraction: float = 0.25      # Quarter Kelly (los pros NUNCA usan más)
    min_edge_required: float = 0.03   # Edge mínimo 3% (bajado de 5% — deportes tienen edges chicos pero consistentes)
    min_win_probability: float = 0.55  # Solo apostar favoritos (>55% prob, subido de 40%)

    # --- Límites por ciclo y diarios ---
    max_bets_per_cycle: int = 5       # Máximo 5 apuestas por ciclo (más oportunidades de deportes)
    max_daily_spend: float = 120.0    # Máximo $120/día (tenemos más capital ahora)
    max_resolution_days: int = 2      # Solo mercados que resuelven en 2 días máx

    # --- Stop-loss automáticos (protección real de capital) ---
    max_daily_loss_pct: float = 0.20    # Parar si pierde 20% en un día
    max_weekly_loss_pct: float = 0.25   # Parar si pierde 25% en una semana
    max_total_loss_pct: float = 0.40    # Kill switch: parar si pierde 40% del ATH
    cooldown_hours_after_stoploss: int = 6  # Pausa de 6 horas tras stop-loss
    max_consecutive_losses: int = 5     # Pausa 30 min después de 5 pérdidas seguidas
    consecutive_loss_pause_minutes: int = 30  # Minutos de pausa tras pérdidas seguidas

    # --- Diversificación ---
    max_open_positions: int = 15          # Máximo 15 posiciones abiertas
    max_exposure_per_category: float = 0.30  # Máximo 30% en un solo tema
    min_market_liquidity: float = 5000    # Mercados con >$5k liquidez
    min_market_volume: float = 2000       # Mercados con >$2k volumen

    # --- Operación ---
    scan_interval_minutes: int = 15       # Escanear cada 15 min (trading corto plazo)
    dry_run: bool = True                  # MODO SIMULACIÓN por defecto
    log_every_decision: bool = True       # Registrar cada decisión

    # --- Categorías permitidas ---
    allowed_categories: List[str] = field(default_factory=lambda: [
        "politics", "economics", "crypto", "sports",
        "science", "technology", "entertainment", "world",
        "general", "pop-culture", "weather", "gaming",
        "business", "finance", "health", "mma", "soccer",
        "basketball", "football", "baseball", "hockey",
        "tennis", "esports", "culture", "media"
    ])


@dataclass
class BotState:
    """Estado actual del bot (se actualiza en tiempo real)."""
    current_bankroll: float = 100.0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    total_pnl: float = 0.0
    open_positions: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    is_paused: bool = False
    pause_reason: str = ""
    consecutive_losses: int = 0          # Contador de pérdidas seguidas
    consecutive_loss_pause_until: float = 0  # Timestamp hasta cuando pausar
    all_time_high: float = 200.0         # ATH del bankroll para kill switch
    last_scan_time: str = ""
    daily_spend: float = 0.0          # Gastado hoy en apuestas nuevas
    daily_spend_date: str = ""        # Fecha del tracking
    cycle_bets: int = 0               # Apuestas en el ciclo actual

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def roi(self) -> float:
        return (self.current_bankroll - 100.0) / 100.0 * 100


# Instancias globales
SAFETY = SafetyRules()
STATE = BotState()
