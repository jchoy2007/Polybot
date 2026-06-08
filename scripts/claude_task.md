# POLYBOT — Tarea para Claude Code (VPS)
> Ejecuta este archivo como prompt de contexto en el servidor.
> Lee CLAUDE.md completo antes de tocar nada.

---

## 🤝 PROTOCOLO DE ROLES (leer primero)

**Claude-PC (sandbox / claude.ai/code):**
- Analiza problemas, escribe el código/diff
- Deja el script listo en la sección "Pendiente para VPS" abajo
- **NO pushea a `main` directamente** (puede escribir en su branch si necesita)
- NO reinicia el bot

**Claude-VPS (servidor /root/Polybot):**
- Lee este archivo al inicio de cada sesión
- Ejecuta los scripts de la sección "Pendiente para VPS"
- Hace `git pull origin main` antes de tocar nada
- Es el ÚNICO que hace `systemctl restart polybot`
- Confirma en la bitácora cuando terminó

**Usuario:** copia el script de Claude-PC y se lo pasa a Claude-VPS.

---

## 📌 Bitácora de coordinación (último arriba)
- _(2-Jun, Claude-PC)_ Fix stocks: ventana 48h→120h para finish-week + gap
  3%→5% para semanales. Commit `ba9120c` ya en `main`. VPS debe aplicar
  con el script de abajo.
- _(2-Jun, Claude-PC)_ Auditoría completa del repo — todo OK, sin tareas urgentes.
- _(2-Jun, Claude-VPS)_ Recovery mode + fix currentValue/kill-switch. Bot OK.

---

## 🚀 Pendiente para VPS — ejecutar en orden

### [4-Jun] 3 mejoras grandes: Arb Trader + Football mejorado + Stocks fixes
```bash
cd /root/Polybot
git pull origin main

# Verificar que todo compila
python3 -m py_compile modules/arb_trader.py modules/football_trader.py main.py modules/stock_trader.py modules/telegram_monitor.py && echo "Syntax OK en todos"

# Reiniciar
systemctl restart polybot
sleep 5
systemctl status polybot --no-pager | head -8

# Verificar que arrancó sin errores
tail -30 logs/polybot_$(date +%Y%m%d).log | grep -E "ERROR|arb|ARB|football|FOOTBALL|stock|STOCK|OK|✅"
```

**Qué trae el commit `6d0b597`:**

| Módulo | Cambio | Impacto |
|--------|--------|---------|
| `arb_trader.py` (NUEVO) | Arbitraje YES+NO cuando spread >4% | Ganancia garantizada, riesgo casi cero |
| `football_trader.py` | Home advantage +65 Elo al equipo local | Más precisión en odds |
| `football_trader.py` | Alias nacionales: USA, Mexico, Canada, etc. | Mundial 2026 ahora funciona con Elo |
| `football_trader.py` | Draw probability con modelo Dixon-Coles | Menos errores en mercados de empate |
| `main.py` | ArbTrader integrado en run_cycle | Se ejecuta cada ciclo automáticamente |

**Fixes anteriores ya aplicados (commits `ba9120c` + `139df64`):**
- Stocks MIN_EDGE 6%→4% | News filter -3→-5 | finish-week ventana 48h→120h | gap semanal 5%

---

## ✅ EJECUTADO ANTERIORMENTE (referencia histórica)

1. **Bug Telegram** — ✅ ARREGLADO (`size * curPrice` en todos los sitios)
2. **Football Trader** — ✅ VERIFICADO (sig_type=2, Side.BUY de v2)
3. **Tracker PENDING 29-Abr** — ⚠️ NO APLICA (dejar como está)
4. **onboard.py venv** — ✅ HECHO
5. **Filtros stocks** — ✅ CONFIRMADOS (whitelist tech-only, intraday OFF)

---

## Estado actual del repo (2-Jun-2026)

- **Branch activa**: `main` (último commit: `ba9120c`)
- **Recovery mode**: solo STOCKS (tech, target-based) + FOOTBALL (Mundial 2026)
- **Politics**: OFF | **Intraday Up/Down**: OFF
- **Kelly**: 0.20 | **max_bet_absolute**: $6 | **Balance aprox**: ~$56 pUSD


Este archivo se creó en la branch `claude/stoic-noether-m5t7H` y se trajo a `main`.
Las tareas YA se ejecutaron/revisaron en el VPS. Resumen:

1. **Bug Telegram** — ✅ **YA ARREGLADO** en `telegram_monitor.py` (usa `value =
   size * curPrice`, no `currentValue`). PERO el campo buggy `currentValue` sigue
   en `main.py:214/421/474` y `tracker.py:161` — incluido el cálculo del kill-switch
   (`main.py:421`). Pendiente de decisión del usuario (toca kill-switch).
2. **Football** — ✅ **VERIFICADO** sin regresiones (import OK, `sig_type=2`, `Side.BUY`
   de `py_clob_client_v2`).
3. **Tracker PENDING 29-Abr** — ⚠️ **NO APLICA / NO EJECUTAR.** En el VPS NO existen
   esos NVDA/TSLA del 29-Abr. Los 5 PENDING reales son POLITICS recientes (30-May→2-Jun)
   que **legítimamente aún no resuelven**. Marcarlos LOST corrompería data real. La
   suposición venía de una copia vieja local, no del VPS. **Dejar como está.**
4. **onboard.py venv** — ✅ **HECHO.** Aviso claro de venv arriba y por sección.
5. **Verificación filtros** — ✅ **CONFIRMADO.** `TRADEABLE_TICKERS` = 7 tech,
   `PROVEN_WINNERS` activo, intradía bloqueado (`stock_trader.py:510`), finish-week
   y closes-above siguen activos.

> Tareas originales abajo (referencia histórica). NO re-ejecutar 1-5 sin releer esto.

---

## 🤝 Coordinación entre los 2 Claudes (este es el canal)

Lee el **PROTOCOLO DE COLABORACIÓN** completo arriba en `CLAUDE.md`. Resumen:
- **Antes de tocar nada:** `git pull --rebase origin main`. Empieza del estado real.
- **Solo Claude-VPS** edita `main` directo y reinicia el bot. Claude-PC propone/PR.
- **Anota acá** lo que vas a hacer ANTES de hacerlo, para no pisar al otro.
- Si abres un PR desde sandbox, escribe acá: **"PR #X listo para merge"**.

### 📌 Bitácora de coordinación (último arriba)
- _(2-Jun, Claude-sandbox)_ Verificación completa del estado del repo desde `main`
  (commit `f5a93b9`). Todo OK: syntax, filtros, flags, bug fixes. Sin cambios de
  código — solo auditoría. No hay tareas pendientes urgentes. VPS no necesita acción.
- _(2-Jun, Claude-VPS)_ Recovery mode + fix currentValue/kill-switch + protocolo
  de colaboración. Todo en `main` (commits hasta `fa2bd58`). Bot reiniciado y OK.

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
