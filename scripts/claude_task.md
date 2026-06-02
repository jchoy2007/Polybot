# POLYBOT — Tarea para Claude Code (VPS)
> Ejecuta este archivo como prompt de contexto en el servidor.
> Lee CLAUDE.md completo antes de tocar nada.

---

## Estado actual del repo (al 2-Jun-2026)

- **Branch activa**: `main`
- **Recovery mode activo**: solo STOCKS (tech, target-based) + FOOTBALL (Mundial 2026)
- **Politics**: OFF (`enable_politics_trader=False`)
- **Intraday Up/Down**: OFF (`enable_intraday_updown=False`)
- **Kelly**: 0.20 | **max_bet_absolute**: $6 | **Balance aprox**: ~$56 pUSD

El tracker local (`data/trade_results.json`) tiene solo 2 trades PENDING del 29-Abr
(NVDA y TSLA Up-or-Down, que era la estrategia antes del fix). El tracker real
con historial completo está en el VPS.

---

## Pendientes en orden de prioridad

### 1. BUG: Telegram muestra valores incorrectos en posiciones perdidas
**Síntoma**: MSFT mostró $21.15 en Telegram cuando Polymarket decía $0.12.
**Archivo**: `modules/telegram_monitor.py`
**Causa probable**: al calcular el valor de una posición LOST, se puede estar
usando el precio de compra o un campo incorrecto en vez del valor de mercado actual.
**Tarea**:
- Leer `telegram_monitor.py` completo
- Encontrar dónde se calcula el valor de posiciones abiertas/perdidas
- Verificar que usa `current_price * shares` (no `cost_basis` ni valor fijo)
- Corregir y hacer syntax check: `python3 -m py_compile modules/telegram_monitor.py`
- Commit: `fix(telegram): corregir valor de posiciones perdidas — usaba campo incorrecto`

---

### 2. FEATURE: Football Trader — validar que el módulo está funcionando bien
**Archivo**: `modules/football_trader.py`
**Tarea**:
- Correr: `python3 -c "from modules.football_trader import FootballTrader; ft = FootballTrader(); print('OK')"`
- Si hay errores de import o config, documentarlos
- Verificar que `_execute_order` usa `sig_type=2` y `Side.BUY` de `py_clob_client_v2`
  (fue reescrito a mano — puede haber regresiones si alguien editó)
- NO cambiar la lógica de Elo ni los thresholds (10%/15%/12%) sin autorización

---

### 3. MEJORA: Tracker — limpiar los 2 trades PENDING inválidos
**Archivo**: `data/trade_results.json`
**Contexto**: hay 2 trades NVDA/TSLA "Up or Down" del 29-Abr marcados como PENDING.
Son de la estrategia intradía que se desactivó. Están resueltos hace semanas.
**Tarea**:
- Revisar cada uno: buscar en Polymarket si ya resolvieron (YES o NO)
- Actualizar `status` a `WON` o `LOST` y agregar `pnl` real
- Si no puedes verificar el resultado, marcarlos como `LOST` (conservador)
  y agregar nota `"note": "manually closed 2-Jun — market likely expired"`
- Hacer backup antes: `cp data/trade_results.json data/trade_results_backup_2jun.json`
- Commit descriptivo con los resultados reales encontrados

---

### 4. MEJORA: onboard.py — fix error "No module named 'dotenv'"
**Archivo**: `scripts/onboard.py`
**Síntoma**: al correr `python3 scripts/onboard.py` (fuera del venv) da error en
el bloque de balance porque intenta importar `dotenv` y `web3` directamente.
**Tarea**: envolver el bloque de balance en try/except más descriptivo y mostrar
mensaje claro: "Corre con el venv: ./venv/bin/python scripts/onboard.py"
en vez de crashear. Ya existe un mensaje parcial, asegurarse que fluye bien.

---

### 5. VERIFICACIÓN: Confirmar que stock_trader.py tiene los filtros correctos
Después de los cambios recientes (whitelist tech-only + intraday OFF), verificar:
```bash
grep -n "TRADEABLE_TICKERS\|enable_intraday_updown\|intraday.*updown\|up.or.down" modules/stock_trader.py | head -20
```
Confirmar que:
- `TRADEABLE_TICKERS` = solo nvda/googl/aapl/tsla/meta/amzn/msft
- La lógica de "Up or Down" está bloqueada cuando `enable_intraday_updown=False`
- El bloque de "finish week" y "closes above/below" sigue activo

---

## Workflow git (recordatorio)

```bash
# Después de cada cambio:
python3 -m py_compile <archivo>.py    # syntax check
git add <archivo>
git commit -m "tipo(scope): descripción — por qué"
git push origin main

# En VPS para aplicar:
git pull origin main && systemctl restart polybot
```

**NUNCA** cambiar sin confirmar con el usuario:
- `max_bet_pct`, `kelly_fraction`, `max_bet_absolute`
- Kill switches o stop-losses
- Reactivar politics o intraday Up/Down

---

## Verificación final después de todos los cambios

```bash
# Estado del servicio
systemctl status polybot

# Últimas líneas del log
tail -50 /root/Polybot/logs/polybot_$(date +%Y%m%d).log

# Balance
./venv/bin/python scripts/daily_audit.py

# Reporte de situación
./venv/bin/python scripts/onboard.py
```
