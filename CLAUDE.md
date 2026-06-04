# PolyBot - Contexto para Claude

> Este archivo contiene todo el contexto del proyecto para que cualquier sesión
> de Claude (local o en el VPS) pueda continuar el trabajo sin perder información.

---

## 🤝 PROTOCOLO DE COLABORACIÓN — 2 Claudes (LEER PRIMERO)

Hay dos sesiones de Claude trabajando este repo. Para NO pisarse:

**Roles:**
- **Claude-VPS** (corre en `/root/Polybot`, en el servidor): hace el deploy real
  (`git pull` + `systemctl restart polybot`). Es el ÚNICO que reinicia el bot.
- **Claude-PC** (claude.ai/code / sandbox): analiza, escribe código, deja el
  script listo en `scripts/claude_task.md`. NO reinicia el bot.

**Reglas de oro:**
1. **Antes de tocar nada:** `git pull --rebase origin main`.
2. **Antes de cada push:** `git pull --rebase origin main` otra vez → `git push`.
3. **Commits chicos y frecuentes.** No acumular sin pushear.
4. **Claude-PC** escribe el diff/script en `claude_task.md` y se lo pasa al usuario.
5. **Claude-VPS** lee `claude_task.md`, ejecuta, confirma en la bitácora.

> Regla simple: **solo Claude-VPS hace `systemctl restart polybot`.**
> Claude-PC propone; Claude-VPS aplica.

---

## 📊 Estado actual del proyecto

### Bankroll (4-Jun-2026)
- **Balance libre**: ~$56.64 pUSD (funder proxy)
- **WR desde 27-Abr**: 62/96 (65%) | Neto: −$54.14 (pérdidas venían de intraday + índices, ya desactivados)
- **Tracker histórico**: `data/trade_results_backup_27apr.json` — gitignored

### Estrategias activas (RECOVERY MODE — desde 2-Jun-2026)
> Solo lo que tiene WR positivo comprobado con capital bajo (~$56).

- ✅ **📈 STOCK TRADER** — activa (lun-vie, 14:00-20:00 UTC).
  - Whitelist tech-only: `nvda/googl/aapl/tsla/meta/amzn/msft`
  - Solo mercados target-based: `"closes above $X"` y `"finish week above $X"`
  - "Up or Down" intradía **DESACTIVADO** (`enable_intraday_updown=False`) — 30% WR, −$57
  - **Conviction sizing:** Tier A (`PROVEN_WINNERS`: nvda/googl/aapl/meta) full stake; Tier B (tsla/amzn/msft) 0.7×
  - Edge mínimo: **4%** (bajado de 6% el 4-Jun — mercados eficientes tienen spread 3-5%)
  - Ventana temporal: **48h** para diarios, **120h** para semanales (fix 4-Jun)
  - Gap máximo: **3%** diarios / **5%** semanales (fix 4-Jun)
  - News filter: solo bloquea si score **≤ −5** (fix 4-Jun; antes −3 bloqueaba todo en silencio)

- ✅ **⚽ FOOTBALL TRADER** — activa. Mundial 2026 + ligas. Ratings Elo de ClubElo.
  - Edge mín: 10% favorito / 15% underdog / 12% empate
  - Ventana: solo entre 24h y 1h antes del kickoff
  - Ejecuta con CLOB v2 (`sig_type=2`, `Side.BUY` de `py_clob_client_v2`)

- ❌ **🏛️ POLITICS TRADER** — DESACTIVADO (`enable_politics_trader=False`). Drenaba capital a edge 1.5-3.4%.
- ⏭️ **🔍 MARKET SCANNER (sports/esports)** — desactivado (Anthropic API sin créditos)
- 🗑️ **CRYPTO** — eliminada definitivamente (28-Abr). 5/14 WR, −$29. Archivo borrado.

**Control por flags** (`config/settings.py` → `SafetyRules`):
`enable_stock_trader`, `enable_football_trader`, `enable_politics_trader`.

### Costos mensuales
- **VPS Hetzner CPX22**: $10.99/mes
- **Anthropic API**: $0 (bot no usa IA — Yahoo Finance + RSS + keyword)
- **Gas Polygon**: ~$2/mes (~50 trades × $0.03)
- **Total**: **~$13/mes**

### Infraestructura
- **VPS**: Hetzner Cloud CPX22 — Helsinki, Finland
- **RPC**: Alchemy Polygon (fallback: public RPC)
- **Wallet EOA**: `0x4bcd692f8F5c18074fF3d37AE3edfB5E826EdC71` (firma órdenes)
- **Polymarket Funder (proxy)**: `0x5718117523abb9648a39374f5d99fcc07c533482` — donde vive el pUSD
- **Servicio**: `systemctl status polybot` (activo 24/7)
- **Venv**: `/root/Polybot/venv/bin/python`
- **Entry point**: `main.py --live`

### Polymarket v2 (desde 27-Abr)
- **SDK**: `py-clob-client-v2` (v1.0.0)
- **Colateral**: **pUSD** (`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`)
- **Auth**: `client.create_or_derive_api_key()` desde la private key
- **`SIGNATURE_TYPE=2`** forzado en `.env` (Browser proxy con funder)
- **Balance reads**: `pUSD.balanceOf(funder)` en todos los sitios
- **Allowances**: aprobadas desde la UI de Polymarket (3 contratos: CTF Exchange, Neg-risk CTF, Neg-risk Adapter)

### Cobro de posiciones v2
- **Automático**: no disponible — el SDK v2 no expone método de redeem, y las posiciones viven en el proxy (no en la EOA)
- **Manual**: polymarket.com → Portfolio → Redeem → aprobar en MetaMask
- **Pendiente**: investigar ABI del proxy para `proxy.exec(CTF, calldata)` y automatizar

---

## 🏗️ Arquitectura del bot

```
/root/Polybot/
├── main.py                  # Orchestrator principal, ciclo cada 15 min
├── redeem.py                # Script standalone de cobro
├── daily_report.py          # Reporte diario (lazy import en main)
├── CLAUDE.md / EMERGENCY.md # Docs operativas
├── config/
│   └── settings.py          # SafetyRules (límites) + BotState + flags on/off
├── core/
│   ├── market_scanner.py    # Busca mercados en Gamma API
│   ├── ai_analyzer.py       # Claude Haiku (sin uso activo — sin créditos)
│   ├── risk_manager.py      # Kelly criterion, stop-loss, cooldowns
│   ├── executor.py          # Órdenes via py-clob-client-v2 (sig_type=2)
│   └── tracker.py           # WON/LOST, win rate (usa size*curPrice, no currentValue)
├── modules/
│   ├── stock_trader.py      # Estrategia stocks: target-based tech only
│   ├── football_trader.py   # Estrategia fútbol: Elo + CLOB v2
│   ├── politics_trader.py   # Politics (desactivado)
│   ├── news_monitor.py      # RSS news filter para stock_trader
│   ├── auto_redeem.py       # Cobra posiciones resueltas (~1h)
│   └── telegram_monitor.py  # Notificaciones + filtros visibles en Telegram
├── scripts/
│   ├── daily_audit.py       # Snapshot: balance, WR, trades 24h, posiciones
│   ├── daily_backup.sh      # Backup data/ (cron 23:00 UTC)
│   ├── daily_review.py      # Resumen diario a Telegram (cron 13:00 y 21:00 UTC)
│   ├── pre_restart_check.py # Validaciones antes de restart
│   ├── whale_monitor.py     # Top whales (cron horario)
│   ├── backtest.py          # Replay de filtros sobre trades históricos
│   ├── onboard.py           # Reporte de situación para sesiones nuevas
│   └── claude_task.md       # Canal de coordinación entre los 2 Claudes
├── data/                    # JSON de estado (tracker, bets_placed, etc.)
└── logs/                    # Logs diarios (gitignored)
```

---

## 🎯 Estrategias activas

### 📈 STOCK TRADER — configuración actual (4-Jun-2026)

**Flow**: Gamma API → filtros keyword → Yahoo Finance → cálculo edge → apuesta CLOB v2

**Tickers tradeables** (`TRADEABLE_TICKERS`):
`nvda`, `googl`, `aapl`, `tsla`, `meta`, `amzn`, `msft`

**Tipos de mercado aceptados** (WR 73% combinado):
- `"Will NVDA finish the week above $X?"` — semanal, ventana 120h
- `"Will AAPL close above $X on [date]?"` — diario, ventana 48h

**Tipos BLOQUEADOS**:
- `"Will NVDA be up or down on [today]?"` — intradía, WR 30%, −$57 histórico

**Filtros en orden de ejecución**:
1. Fin de semana → skip (stocks)
2. Fuera de 14:00-20:00 UTC → skip
3. Whitelist `TRADEABLE_TICKERS` → skip si no está
4. Intraday "up or down" → skip si `enable_intraday_updown=False`
5. VIX > 25 → skip
6. S&P data inaccesible → skip (fail-safe)
7. Gap > 3% diario / > 5% semanal → skip
8. At-the-money trap: precio ≈ target (<1%) + cambio >1.5% → skip
9. Edge < 4% → skip
10. Edge > 40% en cualquier precio → skip (anti-señal del modelo)
11. Cola larga: precio < 0.10 o > 0.90 → skip (salvo extreme-side con prob ≥80%)
12. News muy bearish (score ≤ −5) + S&P no sube → skip UP
13. IA (Claude Sonnet) como último filtro si hay API key — si falla, continúa

**Sizing**:
- Kelly 0.20 × edge × bankroll, capeado a `max_bet_absolute = $6`
- Tier A (PROVEN_WINNERS: nvda/googl/aapl/meta): 1.0× stake
- Tier B (tsla/amzn/msft): 0.7× stake
- Edge ≥ 25%: 0.5× (sizing inverso — edges altos son anti-señal)
- Max 4 bets/día | Daily loss limit: −$35 → pausa resto del día

### ⚽ FOOTBALL TRADER

**Flow**: ClubElo API (ratings Elo) → Polymarket Gamma API → edge check → apuesta CLOB v2

**Filtros**:
- Edge mín favorito: 10% | underdog: 15% | empate: 12%
- Ventana: 24h-1h antes del kickoff
- Solo `sig_type=2`, `Side.BUY` de `py_clob_client_v2`

---

## 🛡️ Reglas de seguridad críticas (config/settings.py)

```python
initial_bankroll = 200.0
min_bet_size = 1.50
max_bet_pct = 0.08           # 8% del bankroll por apuesta
max_bet_absolute = 6.0
kelly_fraction = 0.20        # Conservador (recovery mode)
min_edge_required = 0.03     # 3% global (stocks usa su propio MIN_EDGE=4%)
min_win_probability = 0.55
max_bets_per_cycle = 5
max_daily_spend = 120.0
max_resolution_days = 2      # Stock trader overridea a 5 días para semanales
max_daily_loss_pct = 0.20
max_total_loss_pct = 0.70    # Kill switch dinámico: 70% del ATH desde último arranque
min_market_liquidity = 3000
min_market_volume = 1000
```

**Kill switch real**: NO es un piso fijo. Al arrancar (`main.py`) el ATH se resetea al
balance actual → piso = 70% del total (libre + posiciones). Con $56.64 el piso es ~$40.

---

## ⏰ Cronjobs instalados

```
0 23 * * *  /root/Polybot/scripts/daily_backup.sh >> /root/Polybot/logs/backup.log 2>&1
0 * * * *   /root/Polybot/venv/bin/python /root/Polybot/scripts/whale_monitor.py >> /root/Polybot/logs/whales.log 2>&1
0 13 * * *  /root/Polybot/venv/bin/python /root/Polybot/scripts/daily_review.py >> /root/Polybot/logs/review.log 2>&1
0 21 * * *  /root/Polybot/venv/bin/python /root/Polybot/scripts/daily_review.py >> /root/Polybot/logs/review.log 2>&1
```

`daily_review.py`: manda a Telegram balance + WR por estrategia + apuestas 24h +
alertas automáticas (bot caído, balance <$45, 5 LOSS seguidas, 0 apuestas/24h).

---

## 📊 Análisis de data histórica (n=96 trades, 4-Jun-2026)

### STOCKS — desglose por categoría (n=57 resueltos antes del recovery mode)

| Categoría | WR | P&L |
|---|---|---|
| TECH target-based (nvda/googl/aapl/meta) | **73%** | **+$28.88** ✅ |
| Índices/ETF (S&P/Dow/SPY/Russell) | 33% | −$35.52 ❌ |
| Commodity (WTI/Gold) | 29% | −$51.77 ❌ |
| Up-or-Down intradía | 30% | −$57.12 ❌ |

**Conclusión:** 98% de las pérdidas vienen de intraday + commodities + índices.
Los 3 están bloqueados en recovery mode. Solo tech target-based sigue activo.

### WR general (27-Abr → 4-Jun): 62/96 (65%) | Neto: −$54.14
Las pérdidas son pre-recovery (intraday y categorías ahora bloqueadas).

---

## 🐛 Historial de fixes importantes

### 4-Jun-2026 — commit `139df64`
| Fix | Por qué |
|---|---|
| **MIN_EDGE 6%→4%** | Mercados eficientes tienen spread 3-5%. Con WR 73% el modelo SÍ tiene señal; 6% bloqueaba casi todo silenciosamente. |
| **News filter −3→−5** | Score −3 es ruido normal de titulares. Bloqueaba todos los UP sin aparecer en el contador de Telegram → 0 apuestas invisibles. |
| **Telegram muestra filtros antes invisibles** | Gap, ATM trap, edge insuficiente, 0 mercados, whitelist ahora visibles en el resumen diario. |

### 4-Jun-2026 — commit `ba9120c`
| Fix | Por qué |
|---|---|
| **Ventana 48h→120h para finish-week** | "finish week above $X" resuelve el viernes (~71h desde martes) — antes invisible lun-mié. Estos mercados tienen 73% WR. |
| **Gap 3%→5% para semanales** | `is_weekly` estaba calculado pero nunca usado. Semanales tienen 3-5 días para alcanzar el target; 3% era demasiado estricto. |

### 2-Jun-2026 — commit `b639df3`
| Fix | Por qué |
|---|---|
| **Kill-switch usa `size*curPrice`** | `currentValue` del API de Polymarket se inflaba ($21 vs $0.12 real). Kill-switch veía más capital del real y nunca disparaba. |
| **Tracker usa `size*curPrice`** | Mismo bug: `currentValue` inflado evitaba detectar posiciones LOST. |

### 13-May-2026 — commit `51063c8`
| Fix | Por qué |
|---|---|
| **SPY/QQQ/DIA/IWM usan precio ETF** | Bot fetchaba `^GSPC` (~$7,415) para mercados de SPY ($740). Sonnet recibía "900% above target" y apostaba YES en coin flip. |
| **Regex `closes?` en gap filter** | "closes above $X" (plural) no matcheaba el filtro. SPY y WTI pasaban con gaps at-the-money. |
| **At-the-money trap** | precio ≈ target (<1%) + cambio >1.5% → modelo sobreestima a 78-82% cuando realidad es ~coin flip. |

### 26-Abr-2026 — commit `dbc8fda`
| Fix | Por qué |
|---|---|
| **Stocks bloqueados en fin de semana** | Check de `weekday` estaba anidado dentro del check de horario → 5 apuestas en weekend con datos stale del viernes. |

---

## ⚠️ Patrones a EVITAR (learnings caros)

1. **Intraday Up/Down sin precio objetivo**: edge invertido (perdedoras avg 21% vs ganadoras 11%). El modelo no tiene señal direccional pura. **Bloqueado permanentemente.**
2. **Índices puros y commodities**: WTI −$45, DJIA 0/3, SPY 0/3. **Bloqueados en whitelist.**
3. **Derivados de esports** (Games Total, Map Handicap): 4/5 peores pérdidas históricas. **Bloqueados universalmente.**
4. **Direcciones opuestas mismo ticker**: AMZN Up + AMZN Down = pérdida garantizada.
5. **Auto-seller**: vendía ganadores a +30% cuando cobran +200% al vencer. **Eliminado.**
6. **Edges >40%**: 9 trades edge≥35% → 1W/8L. El modelo "se emociona"; son anti-señal.
7. **Metas irreales**: +16% diario no es alcanzable. Realista: +1.5-3% diario.

---

## 📋 Pendientes

- **Cobro automático v2**: investigar ABI del proxy de Polymarket para `proxy.exec(CTF, calldata)`. Mientras tanto, cobrar manualmente desde polymarket.com.
- **Copy trading de whales**: monitorear top wallets de Polymarket y replicar movimientos. Feature planeada.
- **Auditoría completa del tracker**: data/trade_results.json no auditada con números reales del VPS. Hacer antes de escalar sizing.

---

## 🔧 Comandos útiles (VPS)

```bash
# Logs en vivo
tail -f /root/Polybot/logs/polybot_$(date +%Y%m%d).log

# Logs del servicio
journalctl -u polybot -n 100 --no-pager

# Reiniciar / estado
systemctl restart polybot
systemctl status polybot

# Auditoría completa
cd /root/Polybot && ./venv/bin/python scripts/daily_audit.py

# Cobrar posiciones
cd /root/Polybot && ./venv/bin/python redeem.py

# Balance pUSD en funder
cd /root/Polybot && ./venv/bin/python -c "
import os; from web3 import Web3; from dotenv import load_dotenv; load_dotenv()
w3 = Web3(Web3.HTTPProvider(os.getenv('ALCHEMY_RPC_URL','https://polygon-bor-rpc.publicnode.com')))
funder = os.getenv('POLYMARKET_FUNDER_ADDRESS')
pusd = w3.eth.contract(address=w3.to_checksum_address('0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB'),
  abi=[{'inputs':[{'name':'a','type':'address'}],'name':'balanceOf','outputs':[{'name':'','type':'uint256'}],'type':'function'}])
print(f'pUSD: \${pusd.functions.balanceOf(w3.to_checksum_address(funder)).call()/1e6:.2f}')
"

# Aplicar cambios
cd /root/Polybot && git pull origin main && systemctl restart polybot

# Situación para sesión nueva
cd /root/Polybot && ./venv/bin/python scripts/onboard.py
```

---

## 💡 Notas para Claude (cualquier sesión)

1. **NO cambiar sin confirmar con el usuario**: sizing (`max_bet_pct`, `kelly_fraction`, `max_bet_absolute`), kill switches, stop-losses, reactivar intraday/politics.
2. **Siempre syntax check**: `python3 -m py_compile archivo.py` antes de commitear.
3. **Logging en INFO** (no DEBUG) para filtros críticos — el usuario diagnostica con `grep` sin abrir Claude.
4. **Claude-PC propone, Claude-VPS aplica**. Script en `claude_task.md`, no push directo a main si se puede evitar.
5. **Bot maneja dinero real**: cada cambio debe justificarse con data o bug evidente, no suposiciones.

---

## 📞 Identidad

- **Usuario**: jchoy2007 (GitHub) | Timezone: GMT-5 (Panamá)
- **Wallet**: `0x4bcd692f8F5c18074fF3d37AE3edfB5E826EdC71`
- **Stack**: Python 3 + aiohttp, py-clob-client-v2, web3.py
- **Remote**: `origin` = `github.com/jchoy2007/Polybot` | branch activa: `main`

---

**Última actualización**: 4 junio 2026 — Recovery mode activo. Stocks tech target-based + Football Mundial. 3 bugs críticos resueltos hoy (edge 4%, news −5, ventana finish-week 120h). Balance ~$56.64.
