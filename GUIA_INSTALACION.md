# PolyBot — Guía de Instalación paso a paso

Bot automático de Polymarket con IA. Analiza mercados, detecta value bets y ejecuta apuestas con reglas de seguridad inquebrantables.

---

## Paso 1: Instalar Python

1. Ve a **python.org/downloads** y descarga Python 3.11 o superior
2. Al instalar, **marca la casilla "Add Python to PATH"** (esto es importante)
3. Abre la terminal (CMD o PowerShell) y verifica:

```
python --version
```

Deberías ver algo como `Python 3.11.x` o superior.

---

## Paso 2: Instalar Git

1. Ve a **git-scm.com** y descarga Git para Windows
2. Instálalo con las opciones por defecto
3. Verifica:

```
git --version
```

---

## Paso 3: Crear la carpeta del proyecto

Abre PowerShell o CMD y ejecuta:

```
mkdir C:\PolyBot
cd C:\PolyBot
```

Copia todos los archivos del proyecto dentro de esta carpeta. La estructura debe quedar así:

```
C:\PolyBot\
├── main.py
├── requirements.txt
├── .env.example
├── config\
│   ├── __init__.py
│   └── settings.py
├── core\
│   ├── __init__.py
│   ├── risk_manager.py
│   ├── market_scanner.py
│   ├── ai_analyzer.py
│   └── executor.py
├── logs\
└── data\
```

---

## Paso 4: Instalar dependencias

```
cd C:\PolyBot
pip install -r requirements.txt
```

Si da error, intenta:

```
pip install --break-system-packages -r requirements.txt
```

---

## Paso 5: Crear tu wallet de Polygon

Opción A — MetaMask (más fácil):
1. Instala la extensión MetaMask en Chrome
2. Crea una wallet nueva (guarda tu seed phrase en papel)
3. Agrega la red Polygon (chainId 137)
4. Copia tu clave privada desde MetaMask > Detalles de cuenta > Exportar clave privada

Opción B — Polymarket CLI:
```
npm install -g @polymarket/polymarket-cli
polymarket wallet create
```

---

## Paso 6: Obtener USDC en Polygon

Necesitas $100 en USDC en la red Polygon. Opciones:
1. Compra USDC en un exchange (Binance, Coinbase) y envíalo a tu wallet en red Polygon
2. Compra MATIC en Polygon y cámbialo por USDC en un DEX como Uniswap
3. Usa el bridge de Polymarket si ya tienes USDC en Ethereum

---

## Paso 7: Cuenta en Polymarket

1. Ve a **polymarket.com**
2. Conecta tu wallet MetaMask
3. Acepta los términos de servicio
4. Deposita tu USDC (el bot lo usará desde tu wallet)

---

## Paso 8: Obtener API keys de Polymarket

Desde el CLI de Polymarket:

```
polymarket setup
polymarket clob create-api-key
```

Anota: API Key, Secret, y Passphrase.

---

## Paso 9: Obtener API key de Anthropic (Claude)

1. Ve a **console.anthropic.com**
2. Crea una cuenta
3. Ve a "API Keys" y genera una nueva clave
4. Cópiala (empieza con `sk-ant-...`)

Nota: Claude API tiene costo por uso. Con el bot analizando ~10 mercados cada 30 minutos, el costo estimado es ~$1-3/día usando Claude Sonnet.

---

## Paso 10: Configurar el archivo .env

```
cd C:\PolyBot
copy .env.example .env
notepad .env
```

Rellena cada campo con tus claves reales.

---

## Paso 11: Primer arranque (modo simulación)

```
cd C:\PolyBot
python main.py --scan-only
```

Esto solo escanea mercados sin apostar. Verás una lista de los top mercados.

Luego prueba un ciclo completo en simulación:

```
python main.py --once
```

Esto analiza mercados con IA y simula apuestas sin dinero real.

---

## Paso 12: Modo automático (simulación continua)

```
python main.py --dry-run
```

El bot correrá indefinidamente, escaneando cada 30 minutos y simulando apuestas. Déjalo correr 3-7 días para ver cómo se desempeña antes de activar dinero real.

Revisa los logs en la carpeta `logs/` y los resúmenes en `data/`.

---

## Paso 13: Activar modo LIVE (dinero real)

Solo después de que estés satisfecho con los resultados de simulación:

```
python main.py --live
```

El bot ahora usará dinero real. Las reglas de seguridad siguen activas: máximo $5 por apuesta, stop-loss del 10% diario, y pausa automática si algo sale mal.

---

## Paso 14 (Opcional): Notificaciones por Telegram

1. Abre Telegram y busca a `@BotFather`
2. Envía `/newbot` y sigue las instrucciones
3. Copia el token del bot
4. Busca tu Chat ID con `@userinfobot`
5. Agrega ambos valores al `.env`

Recibirás un resumen de cada ciclo en tu Telegram.

---

## Comandos útiles

| Comando | Qué hace |
|---------|----------|
| `python main.py --scan-only` | Solo escanea mercados |
| `python main.py --once` | Un ciclo completo y sale |
| `python main.py --dry-run` | Simulación continua |
| `python main.py --live` | Dinero real (¡cuidado!) |
| `Ctrl+C` | Detener el bot |

---

## Reglas de seguridad activas

- Máximo $5 por apuesta (5% del bankroll)
- Edge mínimo requerido: 8%
- Kelly fraccional: solo 1/4 del tamaño óptimo
- Stop-loss diario: -10%
- Stop-loss semanal: -15%
- Stop-loss total: -20%
- Máximo 10 posiciones abiertas
- Liquidez mínima del mercado: $10,000
- Pausa automática de 24h tras stop-loss
