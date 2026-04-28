# PolyBot - Contexto para Claude

> Este archivo contiene todo el contexto del proyecto para que cualquier sesión
> de Claude (local o en el VPS) pueda continuar el trabajo sin perder información.

---

## 📊 Estado actual del proyecto

### Bankroll (28-Abr, post migración v2)
- **Depósito en Polymarket v2**: ~$102.72 pUSD (funder proxy)
- **Cash disponible**: ~$101.72 pUSD (1 posición abierta de test manual desde la UI)
- **WR reseteado**: **0/0** desde el fresh-start del 27-Abr 22:30 UTC
- **Tracker histórico**: `data/trade_results_backup_27apr.json` — fuera del repo (gitignored)

### Estrategias activas (post 28-Abr, migración v2)
- ✅ **STOCKS Up/Down only** (única estrategia ejecutora — ventana US 14-20 UTC, lun-vie)
- 👀 **POLITICS monitoring** (loguea, no apuesta — recolectando data)
- ⏭️ **SPORTS desactivada** (Anthropic API billing agotado)
- 🗑️ **CRYPTO eliminada definitivamente** (`modules/crypto_daily.py` borrado el 28-Abr — 5/14 WR 36%, -$29)

### Costos mensuales (post-30 abril)
- **VPS Hetzner CPX22**: $10.99/mes
- **Anthropic API**: $0 (no se usa Haiku — Yahoo + RSS + keyword)
- **Gas Polygon**: ~$2/mes (~50 trades × $0.03)
- **Total mínimo**: **~$13/mes** (Escenario A)
- **Con Claude Pro opcional**: ~$33/mes (Escenario B)

### Infraestructura
- **VPS**: Hetzner Cloud CPX22 — Helsinki, Finland ($10.99/mes)
- **RPC**: Alchemy Polygon (con fallback a public RPC)
- **Wallet EOA**: `0x4bcd692f8F5c18074fF3d37AE3edfB5E826EdC71` (firma órdenes)
- **Polymarket Funder (proxy)**: `0x5718117523abb9648a39374f5d99fcc07c533482` — donde vive el pUSD
- **Servicio**: `systemctl status polybot` (activo 24/7)
- **Venv**: `/root/Polybot/venv/bin/python`
- **Entry point**: `main.py --live` (definido en `/etc/systemd/system/polybot.service`)

### Polymarket v2 (migración 27-28 Abr)
- **SDK**: `py-clob-client-v2` (v1.0.0) — el SDK v1 quedó obsoleto cuando Polymarket migró el CLOB
- **Colateral**: **pUSD** (`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`) — ya no USDC.e
- **Auth**: derivada de la private key con `client.create_or_derive_api_key()`; **no** se usa `RELAYER_API_KEY`
- **`SIGNATURE_TYPE=2`** forzado en `.env` (Browser proxy con funder). Antes el código probaba 0/1/2 — ahora va directo a 2.
- **Balance reads**: `pUSD.balanceOf(funder)` en todos los sitios (`scripts/daily_audit.py`, `main.py` startup + redeem snap, `modules/auto_redeem.py`, `scripts/pre_restart_check.py`)
- **Allowances**: aprobadas desde la UI de Polymarket (3 contratos: CTF Exchange, Neg-risk CTF, Neg-risk Adapter). Re-aprobar si cambian.
- **MarketOrderArgs**: v2 requiere `side` explícito (`Side.BUY` desde `py_clob_client_v2`)
- **Caveat pendiente**: `modules/auto_redeem.py` invoca `redeemPositions` desde la EOA, pero las posiciones v2 viven en el proxy. Cuando haya una posición v2 ganada, hay que verificar si el redeem funciona o requiere firmar desde el proxy.

### Cobro de posiciones (v2 vs v1)

**Posiciones v1 (legacy EOA)**: `redeem.py` las cobra automáticamente cuando el oráculo UMA resuelve. `modules/auto_redeem.py` corre cada ~1h dentro del bot.

**Posiciones v2 (funder/proxy)**: el SDK `py-clob-client-v2` **no expone** método de redeem (verificado: ningún `redeem`, `settle`, `claim`, `merge`, `split`, `withdraw`, `convert` en `dir(ClobClient)`). El cobro automático on-chain falla porque:
1. La posición vive en el funder (smart contract proxy), no en la EOA
2. `CTF.redeemPositions` paga al `msg.sender` — si firma la EOA, los fondos quedan retenidos
3. La EOA tendría que llamar `proxy.exec(CTF, calldata)` — el ABI del proxy de Polymarket no está documentado

**Workaround actual**: cobrar manualmente desde polymarket.com:
1. Abrir polymarket.com → conectar wallet
2. Portfolio → click "Redeem" en cada posición resuelta
3. Aprobar tx en MetaMask

**Pendiente**: investigar el ABI del proxy de Polymarket para implementar `proxy.exec(CTF, calldata)` y volver el cobro automático para v2.

**Nota sobre `redeem.py`**: el bug del `return` temprano en `find_all_positions` se corrigió el 28-Abr — ahora hace **merge** de las posiciones de funder + EOA, deduplicando por `(conditionId, asset)`. Antes solo veía las del primer address con resultados.

### Créditos API
- **Anthropic**: agotado (-$0.01) — bot ya no depende de API
- **Modelo previo**: Claude Haiku 4.5 (queda referenciado en código pero no se invoca con sports/crypto off)

---

## 🏗️ Arquitectura del bot

```
/root/Polybot/
├── main.py                  # Orchestrator principal, ciclo de scan cada 15 min
├── redeem.py                # Script standalone de cobro (subprocess desde main)
├── daily_report.py          # Reporte diario (importado lazy en main para auto-stop)
├── CLAUDE.md / EMERGENCY.md # Docs operativas
├── config/
│   └── settings.py          # SafetyRules (límites inquebrantables) + BotState
├── core/
│   ├── market_scanner.py    # Busca mercados en Gamma API, aplica filtros duros
│   ├── ai_analyzer.py       # Analiza mercados con Claude Haiku (sin uso activo)
│   ├── risk_manager.py      # Kelly criterion, stop-loss, cooldowns
│   ├── executor.py          # Ejecuta órdenes via py-clob-client-v2 (sig_type=2)
│   └── tracker.py           # Rastrea WON/LOST, calcula win rate
├── modules/
│   ├── stock_trader.py      # Estrategia única ejecutora: stocks Up/Down
│   ├── politics_trader.py   # Politics monitoring (no apuesta, recolecta data)
│   ├── news_monitor.py      # RSS news filter para stock_trader
│   ├── auto_redeem.py       # Cobra posiciones resueltas (pUSD desde funder)
│   └── telegram_monitor.py  # Notificaciones a Telegram
├── scripts/
│   ├── daily_audit.py       # Snapshot rápido (balance pUSD, WR, posiciones)
│   ├── daily_backup.sh      # Backup data/ (cron 23:00 UTC)
│   ├── pre_restart_check.py # Validaciones antes de restart
│   ├── whale_monitor.py     # Top whales monitor (cron horario)
│   └── backtest.py          # Replay de filtros sobre trades históricos
├── data/                    # JSON de estado (tracker, bets_placed, etc.)
└── logs/                    # Logs diarios (gitignored)
```

---

## 🎯 Estrategias activas

### Estrategia 1: IA Deportes + Esports (PRINCIPAL)
- **WR histórico**: 8/13 (62%) | P&L: +$16.80
- **Flow**: scanner → filtro sports_kw → IA Haiku → risk check → executor
- **Cubre**: esports (LoL, CS, Dota, Valorant), fútbol europeo + LatAm (Liga MX,
  Brasileirão, Liga Argentina, Colombia), MLS, NBA, NCAAB, EuroLeague, NFL,
  MLB, NHL, MMA, Tennis, Cricket, Rugby, Golf, F1, Chess
- **Filtros críticos**:
  - `market_price >= 0.40` (anti-underdog)
  - `prob_win >= 0.55` (solo favoritos)
  - `edge >= 0.03` (3% mínimo)
  - Resuelve en <40h (margen de seguridad)
  - No mercados de empate ("end in a draw", "draw?")
  - No correlated bets (mismo partido = 1 apuesta máx)

### Estrategia 5: Stock Market Trader
- **WR histórico**: 1/1 (100%) pero pocos datos
- **Cubre**: S&P, NASDAQ, Dow, Russell + NVDA, GOOGL, AAPL, TSLA, META, AMZN, MSFT, NFLX
- **Flow**: Yahoo Finance (datos reales) → compara con Polymarket → apuesta
- **Filtros críticos** (agregados 14-Apr):
  - `min_edge = 0.08` (8% mínimo, más estricto que deportes)
  - `max_bets_per_cycle` se respeta
  - `max_daily_spend` se respeta
  - **NO apostar direcciones opuestas del mismo ticker en el mismo día**
    (commit 5dd5635 — evita AMZN Up + AMZN Down = pérdida garantizada)

### Estrategia 6: Crypto Daily ❌ ELIMINADA (28-Abr)
- WR final: 5/14 (36%), P&L: -$29 — desactivada el 27-Abr, archivo borrado el 28-Abr.
- `modules/crypto_daily.py` ya no existe en el repo. Si se reactiva con otra estrategia
  (ej. latency arb tipo coinman2), recuperar de git history (`git show HEAD~1:modules/crypto_daily.py`).

### Estrategias DESACTIVADAS
- ❌ **WEATHER** (estaba en prueba, eliminada del ciclo)
- ❌ **HARVEST NO** (100% WR pero margen chico, eliminada para reducir complejidad)
- ❌ **AUTO-SELLER** (BUG CRÍTICO: vendía winners a +30% antes de cobrar al 100%)

---

## 🛡️ Reglas de seguridad críticas (config/settings.py)

```python
initial_bankroll = 200.0
min_bet_size = 1.50
max_bet_pct = 0.08           # 8% del bankroll por apuesta
max_bet_absolute = 6.0
kelly_fraction = 0.25        # Quarter Kelly
min_edge_required = 0.03     # 3% edge mínimo (deportes)
min_win_probability = 0.55   # Solo favoritos
max_bets_per_cycle = 5
max_daily_spend = 120.0
max_resolution_days = 2      # Solo mercados <2 días
max_daily_loss_pct = 0.20    # Stop-loss 20% diario
max_total_loss_pct = 0.70    # Kill switch 70% del ATH → $60 con ATH $200 (22-Abr)
min_market_liquidity = 3000  # Relajado de 5k el 14-Apr
min_market_volume = 1000     # Relajado de 2k el 14-Apr
```

---

## 🛡️ Filtros activos (resumen 22 abril 2026)

1. **Weekend mode** — sábado/domingo muchas estrategias skip (mercado EEUU cerrado).
   Stocks **bloqueados completamente** en sáb/dom desde `dbc8fda` (26-Abr).
2. **Max 4 stocks/día** con override si edge > 25% (`stock_trader.py:302-312`)
3. **Horario US 14-20 UTC** — fuera de ventana, stocks skip (`stock_trader.py:416-428`).
   Chequeo de weekday va PRIMERO, luego hora.
4. **Tendencia S&P ±0.5%** con fail-safe (`stock_trader.py:341-376`, 22-Abr)
   - Si Yahoo falla → skip, NO apuesta ciega
   - Log siempre: `📊 S&P tendencia: X.XX%`
5. **VIX < 25** (`stock_trader.py:_get_vix`, 22-Abr)
   - VIX > 30 → skip (pánico) | > 25 → skip (nervioso) | > 20 → log warning
6. **Gap filter 3%** — `close above/below $X` con gap > 3% del precio actual → skip
7. **Colas largas bloqueadas** — YES/NO muy asimétricos (ej. 0.02/0.98) → skip
8. **No direcciones opuestas mismo ticker** (commit 5dd5635)
9. **SPORTS estricto**:
   - `market_price ∈ [0.50, 0.80]`
   - `prob_win ≥ 0.60`
   - `edge ≥ 0.06`
10. **CRYPTO desactivada** temporalmente (3/8 WR, -$12)
11. **Auto-resolve redeem** vía `redeem.py` + cron o subprocess
12. **Política corto plazo habilitada** — aceptar mercados diplomatic/sanctions (commit 691aeb5)
13. **Contador de skips en Telegram** (22-Abr) — el reporte periódico muestra cuántas veces disparó cada filtro en el día

---

## 📈 Balance histórico

| Fecha | Balance | Nota |
|---|---|---|
| 14-Abr | $200 | Depósito inicial |
| 15-Abr | ~$166 | +30% desde mínimo; filtros estrictos ayudaron |
| 17-Abr | ~$166 | Estable |
| 20-Abr | ~$140 | Pre-market stocks perdieron -$34 (5 bets UP en mercado -2%) |
| 21-Abr | $116 | 4/4 stocks LOSS; filtro tendencia S&P no disparaba (silent fail en `logger.debug`) |
| 22-Abr | $102.54 cash + $14 pos ≈ $116 | Filtros reforzados: trend fail-safe + VIX + skip counter |
| 22-Abr (PM) | $74.83 cash / ~$125 total | 11 commits hoy: VIX fix (User-Agent+Stooq) + Telegram logging + Whale monitor + Backtest |

_Añadir filas cada auditoría._

---

## 🧰 Scripts disponibles

| Script | Propósito |
|---|---|
| `scripts/daily_audit.py` | Reporte rápido de balance, WR, trades 24h, posiciones |
| `scripts/daily_backup.sh` | Backup de `data/` (cron 23:00 UTC) |
| `scripts/pre_restart_check.py` | Valida sintaxis, env vars, wallet antes de restart |
| `scripts/whale_monitor.py` | Monitor top whales de Polymarket (cron horario) |
| `scripts/backtest.py` | Aplica filtros actuales retroactivamente vs resultados reales |
| `redeem.py` | Cobrar posiciones resueltas (manual o cron) |

## ⏰ Cronjobs instalados

```
0 23 * * *  /root/Polybot/scripts/daily_backup.sh >> /root/Polybot/logs/backup.log 2>&1
0 * * * *   /root/Polybot/venv/bin/python /root/Polybot/scripts/whale_monitor.py >> /root/Polybot/logs/whales.log 2>&1
```

## 📈 Resultados backtest (22-Abr)

Últimos 30 días, filtros actuales aplicados retroactivamente a `data/trade_results.json`:

| | Trades | Win rate | P&L |
|---|---|---|---|
| **Real** | 50/90 | 55.6% | +$221.15 |
| **Con filtros** | 43/70 | 61.4% | +$266.58 |
| **Mejora** | −20 bloqueados | +5.9pp | +$45.43 |

- **SPORTS pasó de 54% → 75% WR** (el grueso de la mejora)
- **STOCKS** ya estaba bien (60% → 61%)
- **CRYPTO** sin cambio (edge/prob no registrados en trades viejos)
- Top bloqueos: derivados esports (9×), SPORTS fuera rango 0.50-0.80 (5×), SPORTS edge <6% (5×)

---

## 📝 Notas para sesión futura de Claude

**Contexto de subscripción del usuario** (importante para priorizar):
- Subscripción **Max expira 30-Abr-2026** → después el usuario usa Claude Pro (uso limitado de Claude Code).
- Bot debe operar **auto-sostenible** a partir de mayo: mínima intervención humana.
- Rutina diaria del usuario post-Max: **~5 min/día** (audit + redeem si hay 100%).

**Qué esto implica para decisiones de diseño:**
- Preferir **fail-safe** (skip conservador) sobre fail-open (apuesta ciega) en todos los filtros nuevos.
- **Logging verbose** en INFO (no DEBUG) para filtros críticos, así el usuario puede diagnosticar con un `grep` sin Claude.
- **Reportes Telegram auto-contenidos**: todo lo que el usuario necesita ver debe llegarle al celular sin abrir el servidor.
- **Scripts idempotentes** (`daily_audit.py`, `redeem.py`): que puedan correr sin supervisión y dejar output claro.
- **Documentación en CLAUDE.md**: cualquier cambio nuevo se anota aquí, así la próxima sesión tiene el contexto completo sin depender del chat.

**Cosas que NO hacer sin confirmar al usuario:**
- Cambiar sizing (`max_bet_pct`, `kelly_fraction`, `max_bet_absolute`)
- Reactivar auto-seller
- Desactivar kill switches o stop-losses
- Apuestas nuevas sin filtros (ej. crear strategy sin `min_edge`)

---

## 🐛 Bugs arreglados el 14 abril 2026

| Commit | Fix | Por qué |
|--------|-----|---------|
| `141aa1f` | Expandir sports_kw (LatAm + más deportes) | Solo apostaba Europa |
| `59964b4` | Filtros relajados (liquidez/volumen/timing) | 5 markets/ciclo era muy poco |
| `94f94bc` | Stock trades cuentan en `cycle_bets`/`daily_spend` | max_daily_spend no limitaba stocks |
| `955955e` | Tracker registra wins cobrados automáticamente | Win rate quedaba desactualizado |
| `5dd5635` | Bloquear direcciones opuestas mismo ticker mismo día | Google Up + Down garantizaban pérdida |
| `63c1983` | max_open_positions 15→20 | Cap muy bajo, bot frenaba con oportunidades válidas |
| `dee7016` | Bloquear derivados esports (Games Total, Map Handicap) | 3/3 LOST −$23.45 en banda 0.40-0.50 |
| `53f6585` | Stock markets requieren keyword direccional | "Netflix earnings" pasaba el filtro de stocks |
| `52dab9e` | Log edge+prob en cada trade | Audit no podía correlacionar edge con profit |

## 🐛 Bugs arreglados el 15 abril 2026

| Commit | Fix | Por qué |
|--------|-----|---------|
| `ba6162c` | Bloqueo **universal** de derivados (fix regresión `dee7016`) | El filtro anterior requería `is_esports AND is_derivative`, pero markets con `question="Games Total: O/U 2.5"` no traen prefijo del juego y pasaban. Hoy 19:33 UTC un Games Total se ejecutó por este bug. |

## 🐛 Bugs arreglados el 26 abril 2026

| Commit | Fix | Por qué |
|--------|-----|---------|
| `dbc8fda` | Bloquear stocks en fin de semana (`stock_trader.py:416-428`) | El check de `weekday` estaba **anidado dentro** del check de horario US: si la hora caía en 14-20 UTC, saltaba el bloque entero y nunca verificaba sáb/dom. Resultado: 5 apuestas stocks en weekend (2 sáb + 3 dom 26-Abr) usando datos de Yahoo del viernes (mercado cerrado). Total comprometido $40.50 con info stale. Fix: chequear `weekday >= 5` ANTES del horario, retornar `None` con log explícito. Weekend mode de `main.py:358-362` solo ajusta `max_bets/min_edge`, NO bloquea stocks — la defensa correcta es en `stock_trader.py`. |

## 🐛 Bugs pendientes identificados (no arreglados aún)

1. **Telegram muestra valores incorrectos para posiciones perdidas**: MSFT mostró
   $21.15 cuando Polymarket decía $0.12. Causa probable: API de Polymarket
   devolviendo valor incorrecto o telegram_monitor sumando mal.
   
2. **Auditoría completa pendiente**: data/trade_results.json no se ha auditado
   completamente con números reales del VPS. Falta hacerlo antes de escalar.

3. **Copy trading de whales** (feature nueva, planeada para miércoles): monitorear
   wallets de los top traders de Polymarket y replicar sus movimientos en tiempo
   real. Idea del usuario.

---

## ⚠️ Patrones a EVITAR (learnings caros)

1. **Correlación negativa**: apostar UP y DOWN del mismo activo el mismo día
   garantiza perder uno de los dos. Arreglado en stocks, pendiente para deportes
   (aunque ahí es menos común).

2. **Underdogs sin evidencia**: apuestas a <30¢ en deportes con poco contexto
   tienden a perder ~70% del tiempo (datos 14-Apr). El bot ahora requiere >=40¢.

3. **Mercados de empate**: la IA no calibra bien la probabilidad de empate en
   ligas latinoamericanas (25%+ rate de empate real vs IA dice 15%).

4. **Take-profit prematuro**: el auto_seller vendía posiciones ganadoras a +30%
   cuando hubieran cobrado +200% al vencer. Eliminado permanentemente.

5. **Metas irreales**: +16% diario no es alcanzable. Meta realista: +1.5-3% diario
   con el bot bien configurado.

6. **Derivados de esports (Games Total, Map Handicap, Game Handicap)**: alta
   varianza y la IA los mal calibra. **4 de las 5 peores pérdidas históricas**
   son de esa categoría. Bloqueados universalmente desde `ba6162c` (15-Abr).

---

## 💎 Insights de data (15 abril 2026)

Auditoría de los primeros 25 trades resueltos:

1. **STOCKS es la estrategia dominante** (9/10 WR = 90%, +$52.75 neto).
   AMZN invicto 3/3 (+$34.15). Tanto `Up/Down` como `close above/below` funcionan.
   **NO aumentar sizing hasta tener 20+ trades con edge data** (edge solo se
   empezó a registrar el 15-Abr, todavía muestra chica).

2. **SPORTS pesado hacia el rojo** (6/12, −$27.93) pero el −$21.55 de ese total
   viene exclusivamente de esports derivatives. Sin ellos: 2/4 neutral. Hay que
   esperar más data post-filtro para juzgar la estrategia real.

3. **CRYPTO muy chica para concluir** (1/3). Decisión: evaluar cuando llegue
   a n=4 (1 pendiente en curso).

---

## 📋 Decisiones pendientes

- **CRYPTO**: evaluar desactivar cuando llegue a n=4 trades resueltos (ahora n=3).
- **STOCKS sizing**: re-evaluar en 2-3 semanas cuando haya 20+ trades con campo
  `edge` poblado. Mientras tanto no subir `max_bet_pct` ni `kelly_fraction`.
- **Cobro pendiente**: 11 posiciones marcadas RESOLVIDO por Polymarket siguen
  como PENDING en el tracker (entre ellas 1 WIN sin cobrar, Bayern +$3.76).
  Verificar que `auto_redeem` / `redeem.py` está corriendo.

---

## 🔧 Comandos útiles (ejecutar en el VPS)

```bash
# Ver logs del bot en vivo
tail -f /root/Polybot/logs/polybot_$(date +%Y%m%d).log

# Ver logs del servicio (start/stop/crashes)
journalctl -u polybot -n 100 --no-pager

# Reiniciar bot
systemctl restart polybot

# Estado del bot
systemctl status polybot

# Cobrar manualmente
cd /root/Polybot && ./venv/bin/python redeem.py

# Auditoría diaria completa (balance + WR + trades 24h + posiciones abiertas)
cd /root/Polybot && ./venv/bin/python scripts/daily_audit.py

# Auditoría del tracker
cd /root/Polybot && ./venv/bin/python -c "
from core.tracker import WinRateTracker
t = WinRateTracker()
print(t.get_summary())
"

# Ver balance real (pUSD en el funder de Polymarket v2)
cd /root/Polybot && ./venv/bin/python -c "
import os
from web3 import Web3
from dotenv import load_dotenv
load_dotenv()
w3 = Web3(Web3.HTTPProvider(os.getenv('ALCHEMY_RPC_URL', 'https://polygon-bor-rpc.publicnode.com')))
funder = os.getenv('POLYMARKET_FUNDER_ADDRESS')
pusd = w3.eth.contract(
    address=w3.to_checksum_address('0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB'),
    abi=[{'inputs':[{'name':'a','type':'address'}],'name':'balanceOf','outputs':[{'name':'','type':'uint256'}],'type':'function'}]
)
print(f'pUSD (funder): \${pusd.functions.balanceOf(w3.to_checksum_address(funder)).call()/1e6:.2f}')
"

# Aplicar los últimos cambios del repo
cd /root/Polybot && git pull origin main && systemctl restart polybot
```

---

## 📋 Workflow git

- **Branch activa**: `main` (todos los commits van ahí)
- **Branch de fallback**: `claude/polybot-strategy-redeem-fix-MRxIE` (de session anterior)
- **Remote**: `origin` = GitHub `jchoy2007/Polybot`
- **Workflow típico**:
  1. Edit archivo
  2. `python3 -m py_compile archivo.py` (syntax check)
  3. `git add archivo.py`
  4. `git commit -m "..."`
  5. `git push origin main`
  6. En VPS: `git pull && systemctl restart polybot`

---

## 🎯 Siguiente pasos (priorizados)

### Inmediatos
1. **Auditoría completa del tracker** con data real del VPS (no copia local)
2. **Verificar que el fix de correlación** (5dd5635) está aplicado y funcionando
3. **Monitorear win rate por 48h** con los fixes aplicados
4. **Arreglar bug del valor MSFT en Telegram** (muestra valor incorrecto)

### Esta semana
5. **Implementar Copy Trading de whales** (monitorear top traders de Polymarket)
6. **Considerar desactivar Crypto Daily** si no genera suficientes signals
7. **Afinar filtros** si bot toma muy pocas apuestas o demasiadas

### Criterios de parada (kill switches)
- Balance < $80 → pausar bot, auditar
- Balance < $60 → parar bot, decidir si seguir (kill switch automático en `settings.py`: `max_total_loss_pct = 0.70`, 22-Abr)
- WR acumulado < 35% en 20+ trades → revisar estrategia
- >5 pérdidas consecutivas → pausa automática 30 min (ya implementado)

### Criterios de éxito
- Balance > $200 → capital inicial recuperado, considerar retiro parcial
- Balance > $300 → 1.5x, fuerte señal de que funciona
- WR > 55% en 20+ trades → sistema calibrado

---

## 💡 Notas para Claude (VPS o local)

1. **NO hacer cambios sin entender el impacto**: el bot maneja dinero real.
   Cada cambio debe justificarse por bugs o data evidente.

2. **NO deshabilitar stop-losses ni kill switches**: son la protección del
   capital. Si algo "bloquea" una apuesta, es probable que esté bien bloqueada.

3. **Sí hacer**: agregar logging, verificar asumciones, correr pequeñas
   auditorías con `/data/trade_results.json` antes de proponer cambios.

4. **El usuario es bilingüe español/inglés**: responder en el idioma que use.

5. **Commits deben ser descriptivos**: explicar qué se arregla y POR QUÉ.
   Esto es crítico para que el próximo Claude (sesión nueva) entienda el porqué.

6. **Siempre verificar sintaxis después de editar**:
   ```bash
   python3 -m py_compile archivo.py
   ```

7. **Contexto histórico**: el usuario quiere llegar a $500 al 30 abril (meta
   original), pero hemos revisado a $250-300 como realista. NO presionar al
   bot a tomar riesgos malos para alcanzar la meta original.

---

## 📞 Contacto / Identidad

- **Usuario**: jchoy2007 (GitHub)
- **Wallet**: `0x4bcd692f8F5c18074fF3d37AE3edfB5E826EdC71`
- **Timezone**: GMT-5 (Panamá)
- **Stack preferido**: Python 3 + aiohttp, py-clob-client, web3.py

---

**Última actualización**: 28 abril 2026 (post migración Polymarket v2 + cleanup producción)
