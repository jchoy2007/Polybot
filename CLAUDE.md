# PolyBot - Contexto para Claude

> Este archivo contiene todo el contexto del proyecto para que cualquier sesión
> de Claude (local o en el VPS) pueda continuar el trabajo sin perder información.

---

## 📊 Estado actual del proyecto

### Bankroll
- **Depósito inicial**: $200 USDC.e (Polygon)
- **Balance actual**: $45.13 líquido + ~$121 en 24 posiciones abiertas ≈ **$166 total** (15-Abr 20:00 UTC)
- **P&L desde inicio**: -$34 (-17%) — mejora vs -$63 de ayer
- **Meta revisada**: $250-300 al 30 abril 2026 (meta original $500 descartada por no realista)
- **Win rate actual**: 16/25 (64%) | Neto **+$19.28**
  - SPORTS: 6/12 (50%) | −$27.93 | 9 pendientes
  - STOCKS: 9/10 (90%) | +$52.75 | 14 pendientes ← estrella dominante
  - CRYPTO: 1/3 (33%) | −$5.54 | 1 pendiente

### Infraestructura
- **VPS**: Hetzner Cloud CPX22 — Helsinki, Finland ($10.99/mes)
- **RPC**: Alchemy Polygon (con fallback a public RPC)
- **Wallet**: `0x4bcd692f8F5c18074fF3d37AE3edfB5E826EdC71` (EOA)
- **Polymarket Funder**: ver `.env` (POLYMARKET_FUNDER_ADDRESS)
- **Servicio**: `systemctl status polybot` (activo 24/7)
- **Venv**: `/root/Polybot/venv/bin/python`
- **Entry point**: `main.py --live` (definido en `/etc/systemd/system/polybot.service`)

### Créditos API
- **Anthropic**: ~$12 disponibles (~$11 depositados hoy + $1 residual)
- **Modelo**: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`)
- **Costo promedio**: $0.30/día → ~$5 hasta el 30 abril (sobra buffer)

---

## 🏗️ Arquitectura del bot

```
/root/Polybot/
├── main.py                  # Orchestrator principal, ciclo de scan cada 15 min
├── config/
│   └── settings.py          # SafetyRules (límites inquebrantables) + BotState
├── core/
│   ├── market_scanner.py    # Busca mercados en Gamma API, aplica filtros duros
│   ├── ai_analyzer.py       # Analiza mercados con Claude Haiku
│   ├── risk_manager.py      # Kelly criterion, stop-loss, cooldowns
│   ├── executor.py          # Ejecuta órdenes via py-clob-client
│   └── tracker.py           # Rastrea WON/LOST, calcula win rate
├── modules/
│   ├── stock_trader.py      # Estrategia 5: stocks (S&P, QQQ, Dow, NVDA, etc.)
│   ├── crypto_daily.py      # Estrategia 6: BTC/ETH/SOL/XRP diarios
│   ├── auto_redeem.py       # Cobra posiciones resueltas cada ~1h
│   ├── telegram_monitor.py  # Notificaciones a Telegram
│   └── auto_seller.py.disabled  # DESACTIVADO (vendía winners prematuramente)
├── redeem.py                # Script standalone de cobro (subprocess desde main)
├── data/
│   ├── trade_results.json   # Historial de trades (tracker)
│   ├── bets_placed.json     # Market IDs ya apostados
│   └── sold_markets.json    # (obsoleto, era para auto_seller)
└── logs/
    └── polybot_YYYYMMDD.log # Logs del día (formato fecha)
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

### Estrategia 6: Crypto Daily (BTC/ETH/SOL/XRP)
- **WR histórico**: 2/3 (67%) | P&L: +$3.81
- **Flow**: Binance price → compara con Polymarket → apuesta si edge >= 5%
- **Filtros críticos**:
  - Solo mercados que resuelven en <48h (filtro agregado 13-Apr)
  - Prob >= 55% (bloqueo de apuestas con baja convicción)
  - Cooldown 10 min entre apuestas del mismo crypto

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
max_total_loss_pct = 0.40    # Kill switch 40% del ATH
min_market_liquidity = 3000  # Relajado de 5k el 14-Apr
min_market_volume = 1000     # Relajado de 2k el 14-Apr
```

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

# Ver balance real de wallet
cd /root/Polybot && ./venv/bin/python -c "
import os
from web3 import Web3
from dotenv import load_dotenv
load_dotenv()
w3 = Web3(Web3.HTTPProvider(os.getenv('ALCHEMY_RPC_URL', 'https://polygon-bor-rpc.publicnode.com')))
addr = w3.eth.account.from_key(os.getenv('POLYGON_WALLET_PRIVATE_KEY')).address
usdc = w3.eth.contract(
    address=w3.to_checksum_address('0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'),
    abi=[{'inputs':[{'name':'a','type':'address'}],'name':'balanceOf','outputs':[{'name':'','type':'uint256'}],'type':'function'}]
)
print(f'USDC.e: \${usdc.functions.balanceOf(addr).call()/1e6:.2f}')
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

**Última actualización**: 15 abril 2026, 20:00 UTC (post-fix `ba6162c` — bloqueo universal de derivados + audit script)
