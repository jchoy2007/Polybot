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

## Módulos del bot

### 📈 STOCK TRADER (Up/Down) — único módulo ejecutor

Apuesta en mercados *Up or Down* de stocks/índices comparando el precio
actual de Yahoo Finance contra el implícito de Polymarket.

- Solo durante **horario US (14:00-20:00 UTC, 9:30 AM-4:00 PM ET)**
- Solo **lunes a viernes** (mercado US cerrado fines de semana)
- **Máximo 4 apuestas por día**
- Override: si el edge es excepcional (>25%), permite 1 extra
- Cubre índices (S&P, NASDAQ, Dow, Russell) + Mag 7
  (NVDA, GOOGL, AAPL, TSLA, META, AMZN, MSFT, NFLX) + commodities
  (oro, petróleo)

### 🏛️ POLITICS MONITOR

Escanea mercados políticos y geopolíticos. **Loguea pero NO apuesta**
mientras se acumula data histórica para validar la estrategia.

### 🔍 MARKET SCANNER (sports/esports IA)

Escanea mercados de deportes y esports usando Claude (Anthropic API).
**Actualmente desactivado** mientras los créditos de Anthropic estén
agotados. Reactivable poniendo `ANTHROPIC_API_KEY` en `.env`.

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
| Gas Polygon | ~$2-5/mes (~50 trades) |
| Telegram | Gratis |
| Yahoo Finance + RSS | Gratis |
| **Total mínimo** | **~$13-15/mes** |

Si activas `🔍 MARKET SCANNER` con Anthropic API: ~$5-10/mes adicional
(Claude Haiku 4.5).

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
