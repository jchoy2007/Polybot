# PolyBot v10 — Manual de Usuario

## Bot de Trading Automatizado para Polymarket

**Versión:** 10 (Abril 2026)
**Capital recomendado:** $100+ USDC.e
**Horario óptimo:** 9:00 AM - 5:00 PM ET (hora de Panamá)

---

## Qué es PolyBot

PolyBot es un bot que opera automáticamente en Polymarket, la plataforma de mercados de predicción más grande del mundo. Analiza mercados, detecta oportunidades donde el precio de mercado no refleja la probabilidad real, y ejecuta apuestas con dinero real vía la API CLOB de Polymarket.

---

## Estrategias Activas

### 1. IA Value Bets (Claude Haiku 4.5)

**Frecuencia:** Cada 15 minutos
**Costo API:** ~$0.02 por ciclo

Escanea 500 mercados activos, filtra los 10 mejores por liquidez y proximidad a resolución, y envía los top 5 a Claude para análisis profundo. Claude evalúa la probabilidad real vs. el precio de mercado. Si detecta un "edge" mayor al 5%, calcula el monto óptimo con el criterio de Kelly y ejecuta la apuesta.

**Filtros de seguridad:**
- Liquidez mínima: $5,000
- Volumen mínimo: $2,000
- Probabilidad de ganar > 55%
- Edge mínimo: 5%
- Máximo $10 por apuesta (con bankroll < $200)

### 2. Crypto Sniper (BTC/ETH/SOL)

**Frecuencia:** Cada 5 segundos
**Costo API:** $0 (no usa Claude)

Monitorea precios de BTC, ETH y SOL en Binance en tiempo real. Busca mercados "Up or Down" de 15 minutos en Polymarket. **Solo entra en los últimos 2 minutos** del período de 15 min, cuando ya tiene 13 minutos de datos confirmando la dirección del precio.

**Lógica de entrada:**
- Calcula momentum en ventanas de 10s, 30s, 60s y 120s
- Verifica que el mercado cierra dentro de 2 minutos
- Compara precio de Binance vs. probabilidad de Polymarket
- Si hay desfase (edge > 8%), ejecuta inmediatamente
- Intenta FOK (Fill-Or-Kill), si falla intenta GTC (limit)

**Cryptos monitoreadas:**
- BTC (BTCUSDT en Binance)
- ETH (ETHUSDT)
- SOL (SOLUSDT)

### 3. Auto-Cobro de Ganancias

**Frecuencia:** Cada ciclo (15 min) en modo LIVE
**Costo API:** $0

Revisa automáticamente si hay mercados resueltos donde tengas tokens ganadores. Si los encuentra, ejecuta `redeemPositions` en el smart contract CTF de Polygon para cobrar USDC.e directo a tu wallet.

**Soporta:**
- Mercados estándar (CTF)
- Mercados NegRisk (usa NegRiskAdapter)

---

## Archivos del Proyecto

```
C:\PolyBot\
├── .env                    # Configuración secreta (API keys, wallet)
├── main.py                 # Bot principal (ejecutar este)
├── check_bets.py           # Verificar balance y posiciones
├── redeem.py               # Cobrar ganancias manualmente
├── setup_polymarket.py     # Configuración inicial (una vez)
├── sell_all.py             # Vender posiciones abiertas
├── config/
│   └── settings.py         # Reglas de seguridad y configuración
├── core/
│   ├── market_scanner.py   # Escanea mercados de Polymarket
│   ├── ai_analyzer.py      # Análisis con Claude (Haiku 4.5)
│   ├── risk_manager.py     # Gestión de riesgo y Kelly
│   └── executor.py         # Ejecuta órdenes reales en CLOB
└── modules/
    ├── btc_15min.py         # Estrategia crypto 15-min
    ├── crypto_sniper.py     # Sniper de latencia (cada 5s)
    └── auto_redeem.py       # Auto-cobro de ganancias
```

---

## Comandos

### Operación diaria

```bash
# Modo LIVE - un solo ciclo (para probar)
python main.py --live --once

# Modo LIVE - continuo (operación normal)
python main.py --live

# Modo simulación (no gasta USDC, sí gasta API)
python main.py --once

# Solo escanear mercados (no apuesta)
python main.py --scan-only
```

### Utilidades

```bash
# Verificar balance y posiciones
python check_bets.py

# Cobrar ganancias manualmente
python redeem.py

# Vender posiciones abiertas
python sell_all.py

# Configuración inicial (una sola vez)
python setup_polymarket.py
```

### Detener el bot

Presiona `Ctrl+C` en la terminal. El bot guarda un resumen en `data/summary_FECHA.json` al cerrar.

---

## Configuración Inicial (una vez)

### Requisitos
- Python 3.10+
- MetaMask con wallet en Polygon
- USDC.e depositado en Polymarket
- POL para gas (~0.01 POL mínimo)
- API key de Anthropic (Claude)

### Pasos

1. **Instalar dependencias:**
```bash
pip install py-clob-client python-dotenv aiohttp web3 anthropic
```

2. **Crear archivo .env:**
```bash
copy .env.example .env
```

3. **Configurar .env con tus claves:**
```
ANTHROPIC_API_KEY=sk-ant-...tu_clave...
POLYGON_WALLET_PRIVATE_KEY=0x...tu_clave_privada...
POLYMARKET_FUNDER_ADDRESS=0x...tu_dirección_proxy...
```

4. **Generar API keys de Polymarket:**
```bash
python setup_polymarket.py
```

5. **Depositar USDC en Polymarket** desde polymarket.com

6. **Probar en simulación:**
```bash
python main.py --once
```

7. **Primer trade real:**
```bash
python main.py --live --once
```

### Cómo encontrar tu dirección proxy (POLYMARKET_FUNDER_ADDRESS)
1. Ve a polymarket.com
2. Click en tu ícono de perfil (arriba derecha)
3. Click en tu nombre/dirección
4. Copia la dirección que empieza con 0x...

---

## Horario Recomendado

| Horario (ET/Panamá) | Actividad | Recomendación |
|---|---|---|
| 9:00 AM - 5:00 PM | Mercado activo | Bot encendido (`--live`) |
| 5:00 PM - 9:00 PM | Actividad reducida | Opcional |
| 9:00 PM - 9:00 AM | Noche | Bot apagado (ahorra API) |

**¿Por qué apagarlo de noche?**
- Pocos mercados con edge → mismos 5 mercados repetidos
- Gasto de API sin retorno (~$0.02/ciclo × 4/hora = desperdicio)
- El sniper crypto funciona 24/7 pero sin la IA es menos efectivo

---

## Reglas de Seguridad (config/settings.py)

Estas reglas **no se pueden romper** por código:

| Regla | Valor | Descripción |
|---|---|---|
| max_bet_absolute | $10.00 | Máximo por apuesta individual |
| max_bet_pct | 10% | Máximo % del bankroll por apuesta |
| min_edge_required | 5% | Edge mínimo para apostar |
| kelly_fraction | 0.50 | Usa medio Kelly (conservador) |
| max_daily_loss_pct | 15% | Pausa si pierde 15% en un día |
| max_weekly_loss_pct | 25% | Pausa si pierde 25% en semana |
| max_open_positions | 15 | Máximo posiciones simultáneas |
| cooldown_hours | 12 | Pausa 12h después de stop-loss |

---

## Costos de API (Anthropic)

| Concepto | Costo |
|---|---|
| Modelo | Claude Haiku 4.5 |
| Input | $1.00 / millón de tokens |
| Output | $5.00 / millón de tokens |
| Por ciclo (~5 análisis) | ~$0.02 |
| Por hora (4 ciclos) | ~$0.09 |
| Día completo (9AM-5PM) | ~$0.70 |
| **$7.77 de saldo** | **~10 días de operación** |

**Nota:** Solo la estrategia IA consume tokens. El sniper y auto-cobro son gratis.

---

## Verificar Resultados

### En la terminal
El bot muestra un resumen al final de cada ciclo con bankroll, trades ejecutados y P&L.

### En Polymarket
- Ve a polymarket.com → tu perfil → Posiciones
- Tab "Activa" = apuestas pendientes
- Tab "Cerrado" = resultados finales

### Con el script
```bash
python check_bets.py
```
Muestra tu balance real de USDC.e en Polygon.

---

## Solución de Problemas

| Problema | Causa | Solución |
|---|---|---|
| "ANTHROPIC_API_KEY no encontrada" | .env no configurado | Verifica que .env tiene tu API key |
| "Error Claude API 529" | Servidores de Claude ocupados | Normal, el bot reintenta automáticamente |
| "Sin señales en BTC/ETH/SOL" | No hay mercados crypto activos ahora | Normal fuera de horario de mercado |
| "Sin apuestas de valor" | Ningún mercado tiene edge suficiente | Normal, el bot es selectivo |
| "No se pudo conectar a Polygon" | Problema de red | Verifica internet, el bot prueba múltiples RPCs |
| "invalid signature" al vender | Config de firma incorrecta | Verificar POLYMARKET_FUNDER_ADDRESS en .env |

---

## Próximas Mejoras (Roadmap)

- [ ] Copy Trading: seguir traders con alto win rate
- [ ] Horario automático: sleep fuera de horario de mercado
- [ ] Mercados de 5 minutos y 1 hora (además de 15 min)
- [ ] Dashboard web para monitorear desde el celular
- [ ] Alertas por Telegram cuando ejecuta un trade
- [ ] Histórico de P&L y gráficos de rendimiento

---

## Historial de Versiones

| Versión | Fecha | Cambios |
|---|---|---|
| v10 | Abr 2, 2026 | Fix timing crypto (solo últimos 2 min), auto-cobro, manual |
| v9 | Abr 1, 2026 | Crypto Sniper BTC/ETH/SOL, auto-redeem |
| v8 | Mar 31, 2026 | Ejecutor real con py-clob-client |
| v7 | Mar 31, 2026 | Fix copy trading, arbitraje, umbrales BTC |
| v6 | Mar 31, 2026 | Multi-estrategia (4 estrategias) |
| v1-v5 | Mar 31, 2026 | Setup inicial, IA analyzer, risk manager |
