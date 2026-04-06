"""
PolyBot - Script de Limpieza (Día 3)
======================================
Elimina archivos que ya no son necesarios.

EJECUTAR DESDE: C:\PolyBot\
COMANDO: python cleanup.py

ANTES DE EJECUTAR:
- Asegúrate de tener backup del zip original
- Revisa la lista de archivos a eliminar abajo
"""

import os
import shutil
from pathlib import Path

# ============================================================
# ARCHIVOS A ELIMINAR
# ============================================================

FILES_TO_DELETE = [
    # Scripts one-time que ya se usaron (días 1-2)
    "fix_allowance.py",         # Fix NegRisk allowance - ya corrido
    "fix_signature.py",         # Diagnóstico de firma - ya corrido
    "diagnose_crypto.py",       # Diagnóstico crypto - ya corrido
    "cobrar.py",                # Versión vieja de redeem.py
    "redeem_btc.py",            # Redeem de un mercado específico BTC
    
    # Archivo .py en carpeta equivocada
    "logs/crypto_sniper.py",    # Copia del sniper en logs (error)
    
    # Módulo desactivado
    "modules/crypto_sniper.py", # Desactivado del loop desde Día 2
]

# Carpetas de caché Python (se regeneran solas)
PYCACHE_DIRS = [
    "config/__pycache__",
    "core/__pycache__",
    "modules/__pycache__",
]

# Summaries viejos (mantener solo los últimos 5)
KEEP_LAST_SUMMARIES = 5


def main():
    print("=" * 50)
    print("🧹 PolyBot - Limpieza de Archivos")
    print("=" * 50)
    print()

    deleted = 0
    
    # 1. Eliminar archivos individuales
    print("📁 Archivos a eliminar:")
    for f in FILES_TO_DELETE:
        path = Path(f)
        if path.exists():
            print(f"   ❌ {f}")
            path.unlink()
            deleted += 1
        else:
            print(f"   ⏭️  {f} (no existe)")
    
    print()
    
    # 2. Limpiar __pycache__
    print("📁 Limpiando __pycache__:")
    for d in PYCACHE_DIRS:
        path = Path(d)
        if path.exists():
            shutil.rmtree(path)
            print(f"   ❌ {d}/")
            deleted += 1
        else:
            print(f"   ⏭️  {d}/ (no existe)")
    
    print()
    
    # 3. Limpiar summaries viejos
    print(f"📁 Limpiando summaries (mantener últimos {KEEP_LAST_SUMMARIES}):")
    summary_dir = Path("data")
    if summary_dir.exists():
        summaries = sorted(summary_dir.glob("summary_*.json"))
        if len(summaries) > KEEP_LAST_SUMMARIES:
            to_delete = summaries[:-KEEP_LAST_SUMMARIES]
            for s in to_delete:
                s.unlink()
                deleted += 1
            print(f"   ❌ Eliminados {len(to_delete)} summaries viejos")
            print(f"   ✅ Mantenidos {KEEP_LAST_SUMMARIES} más recientes")
        else:
            print(f"   ✅ Solo hay {len(summaries)} (nada que limpiar)")
    
    print()
    print(f"🧹 Total eliminados: {deleted} archivos/carpetas")
    print()
    
    # 4. Mostrar estructura final
    print("📂 ESTRUCTURA FINAL LIMPIA:")
    print("=" * 50)
    print("""
C:\\PolyBot\\
├── .env                        # Claves (NO tocar)
├── main.py                     # ★ REEMPLAZADO (5 estrategias)
├── redeem.py                   # Cobrar posiciones ganadoras
├── sell_all.py                 # Vender todas las posiciones
├── check_bets.py               # Verificar balance y apuestas
├── setup_polymarket.py         # Setup inicial
├── requirements.txt            # Dependencias
├── MANUAL_POLYBOT.md           # Manual de uso
├── GUIA_INSTALACION.md         # Guía de instalación
│
├── config/
│   ├── __init__.py
│   └── settings.py             # Configuración (sin cambios)
│
├── core/
│   ├── __init__.py
│   ├── ai_analyzer.py          # Análisis con Claude
│   ├── executor.py             # Ejecutor de trades
│   ├── market_scanner.py       # Scanner de mercados
│   ├── risk_manager.py         # Gestión de riesgo
│   └── tracker.py              # Win rate tracker
│
├── modules/
│   ├── __init__.py
│   ├── btc_15min.py            # Estrategia 2: Crypto 15-min
│   ├── no_harvester.py         # Estrategia 3: NO Harvester
│   ├── weather_trader.py       # ★ NUEVO - Estrategia 4: Clima
│   ├── stock_trader.py         # ★ NUEVO - Estrategia 5: Bolsa
│   ├── auto_redeem.py          # Auto-cobro (desactivado)
│   ├── arbitrage.py            # Arbitraje (futuro)
│   └── copy_trading.py         # Copy trading (futuro)
│
├── data/
│   ├── bets_placed.json        # Anti-duplicado
│   └── trade_results.json      # Win rate tracking
│
└── logs/
    └── polybot_YYYYMMDD.log    # Logs diarios
""")


if __name__ == "__main__":
    confirm = input("¿Ejecutar limpieza? (s/n): ").strip().lower()
    if confirm in ("s", "si", "sí", "y", "yes"):
        main()
    else:
        print("Cancelado.")
