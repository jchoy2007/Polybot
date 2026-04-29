# PolyBot — Manual del Usuario

Guía de operación diaria. Si estás instalando por primera vez, lee
**INSTALL.md** primero.

---

## ¿Qué hace el bot?

Apuesta automáticamente en mercados de predicción de
[Polymarket](https://polymarket.com) basándose en datos reales de
stocks (S&P, NASDAQ, NVIDIA, Apple, Tesla, etc.) y monitorea política
y geopolítica para acumular data.

---

## Motores

### 📈 STOCK TRADER (Up/Down + IA Sonnet)

Apuesta en mercados *Up or Down* de stocks/índices comparando el precio
actual de Yahoo Finance contra el implícito de Polymarket.

- Solo durante **horario US (14:00-20:00 UTC, 9:30 AM-4:00 PM ET)**
- Solo **lunes a viernes** (mercado US cerrado fines de semana)
- **Máximo 4 apuestas por día** (override por edge >25%)
- Cubre índices (S&P, NASDAQ, Dow, Russell) + Mag 7
  (NVDA, GOOGL, AAPL, TSLA, META, AMZN, MSFT, NFLX) + commodities
- **Último filtro Claude Sonnet 4.6**: revisa cada apuesta antes de
  ejecutar y veta si ve catalizador adverso o riesgo de fade

### 🏛️ POLITICS TRADER (regla extrema + IA Sonnet)

Apuesta en mercados políticos/geopolíticos donde el precio implica un
desenlace casi seguro. La IA confirma cada apuesta antes de ejecutar.

- Solo si **YES ≥ 0.85** (apuesta YES) o **YES ≤ 0.15** (apuesta NO)
- Liquidez > $5,000, resuelve en **<3 días**
- **Máximo 2 apuestas por día**, $2 por bet (sin Kelly hasta validar n≥5)
- **Último filtro Claude Sonnet 4.6**: veta si detecta poll/leak adverso,
  ambigüedad de resolución o whale anchoring

### 📰 NEWS FILTER (sentiment para stocks)

Lee headlines de Yahoo Finance, Bloomberg, MarketWatch y WSJ. Calcula un
score (bullish - bearish) y bloquea apuestas en stocks contra la tendencia
de noticias (UP si score ≤ -3, DOWN si score ≥ +3).

### 🔍 MARKET SCANNER (sports/esports)

Escanea mercados de deportes y esports. **Desactivado** intencionalmente:
sin reglas determinísticas para sports (a diferencia de stocks/politics),
la IA sola no compensa el costo + complejidad de filtros para derivados,
empates y spreads. Pendiente revisar tras validar politics.

> **Sin `ANTHROPIC_API_KEY`** el bot opera con filtros base (sin la capa de
> IA). Stocks y politics siguen funcionando, pero pierdes la última
> revisión cualitativa antes de cada ejecución.

---

## Reglas de seguridad activas

El bot tiene **15+ filtros** que bloquean apuestas inseguras. Cada uno
ya disparó alguna vez en producción y está justificado:

1. **Horario de mercado US** — fuera de 14-20 UTC, stocks skip
2. **Bloqueo fines de semana** — sáb/dom no opera (datos del viernes
   son stale)
3. **Filtro VIX < 25** — alta volatilidad → skip
4. **Filtro tendencia S&P ±0.5%** — si Yahoo falla, skip (fail-safe)
5. **Filtro News RSS sentiment** — noticias bearish bloquean entradas
6. **Daily loss limit −$25 neto** — pérdida diaria stocks → pausa
7. **Max 4 stocks/día** — con override por edge >25%
8. **Max 8% del bankroll por apuesta** — `max_bet_pct = 0.08`
9. **Tamaño Quarter Kelly** — `kelly_fraction = 0.25`
10. **No apuestas opuestas mismo ticker mismo día** — evita NVDA Up +
    NVDA Down (pérdida garantizada)
11. **Edge mínimo 8%** stocks (3% sports, cuando reactives)
12. **Probabilidad mínima 55%** — solo favoritos, nunca underdogs
13. **Liquidez mínima $3,000 / volumen $1,000** por mercado
14. **Resolución <48h** — solo apostar mercados que cierran pronto
15. **Kill switch −70% del ATH** — `max_total_loss_pct = 0.70`
16. **Stop-loss diario −20%** — `max_daily_loss_pct = 0.20`
17. **Cooldown 30 min tras 5 pérdidas seguidas**

> Estos valores se modifican en `config/settings.py`. **NO los relajes
> sin entender por qué fueron puestos** — están documentados en
> `CLAUDE.md`.

---

## Reportes Telegram

### Cada hora (reporte periódico)

- Balance actual (pUSD en el funder)
- Posiciones abiertas (ganando/perdiendo)
- Filtros que actuaron en el día y conteo
- Win rate histórico

### Cuando ejecuta

- 📈 **Trade ejecutado** con detalles (mercado, lado, precio, edge, monto)
- 💰 **Cobro exitoso** cuando una posición gana y se redime
- 🚨 **Error** si una orden falla, RPC cae, etc.

---

## Cobro de ganancias

El bot intenta cobrar automáticamente cada hora vía `redeem.py` /
`auto_redeem.py`. Si una posición v2 (proxy) no se cobra automáticamente
(workaround pendiente — ver CLAUDE.md), cobra manualmente:

1. Abrir [polymarket.com](https://polymarket.com)
2. Conectar tu wallet
3. Ir a **Portfolio**
4. Click **Redeem** en cada posición resuelta

---

## Costos mensuales

| Concepto | Costo |
|---|---|
| VPS Hetzner CPX22 | ~$11/mes |
| Anthropic API (Claude Sonnet 4.6, último filtro) | ~$7/mes |
| Gas Polygon | ~$2/mes (~50 trades) |
| Telegram | Gratis |
| Yahoo Finance + RSS news | Gratis |
| **Total** | **~$20/mes** |

Sin Anthropic API el bot opera solo con filtros base: ~$13/mes (pierdes la
capa de IA, pero stocks y politics siguen ejecutando).

---

## Cómo ver tu progreso

Para ver tu progreso usa Telegram (reportes cada hora) o polymarket.com (Portfolio).

---

## Resolución de problemas

### Bot no opera (cero apuestas en horario)

1. `systemctl status polybot` — debe estar **active**
2. Si dice **failed**: `journalctl -u polybot -n 100 --no-pager`
3. Si **active** pero no apuesta:
   - ¿Estás en horario US 14-20 UTC lun-vie?
   - ¿Hay mercados Up/Down disponibles?
     `tail -f logs/polybot_$(date +%Y%m%d).log` y ver el ciclo siguiente.
   - VIX/news/tendencia pueden estar bloqueando.

### Sin notificaciones Telegram

1. Verificar `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en `.env`
2. Mandarle `/start` al bot desde tu Telegram
3. Probar manual:
   ```bash
   curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
        -d "chat_id=<CHAT_ID>&text=test"
   ```

### Balance no actualiza

1. Verificar que el dinero esté en el **funder** (proxy), no en la EOA:
   ```bash
   ./venv/bin/python scripts/daily_audit.py
   ```
2. Si `daily_audit` muestra balance correcto pero el log no, reiniciar:
   `systemctl restart polybot`

### Posición ganada no se cobra

Las posiciones v2 (proxy) tienen un workaround manual pendiente. Ver
`CLAUDE.md` sección "Cobro de posiciones (v2 vs v1)". Cobrar manualmente
en polymarket.com.

---

## Soporte

Si tu instalación quedó funcionando pero algo deja de andar:

1. Revisa **EMERGENCY.md** (rollback rápido).
2. Revisa **CLAUDE.md** (contexto completo del proyecto).
3. Contacto: *[completar email/canal de soporte aquí]*
