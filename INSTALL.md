# PolyBot — Guía de Instalación

Bot de trading automatizado para Polymarket. Esta guía te lleva desde cero
hasta verlo operando en producción.

## Tiempo estimado: 30-45 minutos

---

## Requisitos previos

### 1. Servidor VPS (recomendado)

- **Hetzner Cloud CPX22** (~$11/mes) o equivalente
- **Linux Ubuntu 22.04+**
- **2 GB RAM mínimo** (4 GB recomendado)
- **20 GB disco mínimo**

> Alternativa: cualquier máquina Linux con conexión 24/7 (Raspberry Pi 4 sirve).

### 2. Cuentas necesarias

| Servicio | Para qué | Costo |
|---|---|---|
| GitHub | Clonar el repo | Gratis |
| Polymarket.com | Plataforma de apuestas + wallet conectada | Gratis |
| MetaMask | Firmar transacciones (wallet) | Gratis |
| Telegram | Notificaciones del bot | Gratis |
| Anthropic API | Solo si activas `🔍 MARKET SCANNER` (sports IA) | Pay-as-you-go |

### 3. Wallet con fondos

- **Mínimo $100 en pUSD** dentro de Polymarket (depositados desde MetaMask)
- **Mínimo $1 en POL/MATIC** en MetaMask para gas

---

## Paso 1 — Crear wallet (si no tienes)

1. Instalar [MetaMask](https://metamask.io) en el navegador.
2. Crear wallet nueva. **Guardar la frase semilla en lugar seguro** (papel,
   gestor de contraseñas — NUNCA en la nube sin cifrar).
3. Agregar red Polygon:
   - Network name: `Polygon Mainnet`
   - RPC: `https://polygon-rpc.com`
   - Chain ID: `137`
   - Symbol: `POL`
   - Block explorer: `https://polygonscan.com`

---

## Paso 2 — Depositar fondos en Polymarket

1. Ir a [polymarket.com](https://polymarket.com).
2. Click **Sign In** → conectar MetaMask.
3. Click **Deposit** y depositar USDC desde un exchange (Binance, Coinbase,
   Kraken). Polymarket lo convertirá a **pUSD** automáticamente.
4. Una vez que se vea balance en Polymarket, **anota tu Polymarket Funder
   Address (proxy)** — está en el menú de la wallet (formato `0x...`). Esta
   dirección es donde vivirá el dinero, NO la dirección de MetaMask.

---

## Paso 3 — Crear bot de Telegram

1. En Telegram, buscar `@BotFather` y abrir chat.
2. Enviar `/newbot` y seguir las instrucciones (elegir nombre + username).
3. **Guardar el token** que entrega (formato `123456:ABC...`).
4. Hablar con tu bot recién creado y enviar `/start`.
5. Para obtener tu `chat_id`: abrir
   `https://api.telegram.org/bot<TOKEN>/getUpdates` en navegador. Copiar el
   número en `"chat":{"id":XXXXXXX,...`.

---

## Paso 4 — Configurar VPS

```bash
ssh root@TU_IP

# Actualizar sistema
apt update && apt upgrade -y
apt install -y git python3 python3-venv python3-pip

# Clonar repo
cd /root
git clone https://github.com/TU_USUARIO/Polybot.git
cd Polybot

# Crear entorno virtual e instalar dependencias
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

---

## Paso 5 — Configurar `.env`

```bash
cp .env.example .env
nano .env
```

Llenar:

```env
POLYGON_WALLET_PRIVATE_KEY=0x_TU_PRIVATE_KEY_DE_METAMASK
POLYMARKET_FUNDER_ADDRESS=0x_TU_PROXY_DE_POLYMARKET
ALCHEMY_RPC_URL=https://polygon-rpc.com
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=TU_CHAT_ID
SIGNATURE_TYPE=2
ANTHROPIC_API_KEY=     # Opcional — dejar vacío si no usas IA
```

> **Cómo exportar la private key de MetaMask**: MetaMask → tres puntos →
> Account details → Show private key (pide la contraseña). NUNCA compartas
> esto. Si te la piden por mensaje, es estafa.

---

## Paso 6 — Instalar como servicio systemd

```bash
cp polybot.service.example /etc/systemd/system/polybot.service
systemctl daemon-reload
systemctl enable polybot
systemctl start polybot
```

---

## Paso 7 — Verificar

```bash
systemctl status polybot
./venv/bin/python scripts/daily_audit.py
```

Si:
- `systemctl status` dice **active (running)** ✓
- Llega un mensaje de inicio a tu Telegram ✓
- `daily_audit.py` muestra tu balance ✓

→ **Instalación exitosa.**

---

## (Opcional) Paso 8 — Dashboard web

```bash
./venv/bin/python dashboard/server.py
```

Acceder desde el navegador:
- Si VPS: `http://TU_IP:5000`
- Si local: `http://localhost:5000`

> Para que el dashboard quede corriendo en background como servicio, puedes
> crear un segundo unit systemd `polybot-dashboard.service` apuntando a
> `dashboard/server.py`.

---

## Comandos importantes

```bash
# Encender bot
systemctl start polybot

# Apagar bot
systemctl stop polybot

# Reiniciar bot
systemctl restart polybot

# Ver logs en vivo
tail -f /root/Polybot/logs/polybot_$(date +%Y%m%d).log

# Auditoría diaria (balance, WR, posiciones)
cd /root/Polybot && ./venv/bin/python scripts/daily_audit.py

# Cobrar ganancias (posiciones resueltas)
cd /root/Polybot && ./venv/bin/python redeem.py
```

---

## Problemas comunes

### Bot no arranca

```bash
journalctl -u polybot -n 50 --no-pager
```

Suele ser:
- `.env` mal formado o vacío
- Dependencia faltante → `./venv/bin/pip install -r requirements.txt`
- Permisos del archivo `.env` → `chmod 600 .env`

### "Insufficient balance" al apostar

- Verificar que el dinero esté en el **proxy** (funder), no en la EOA.
- Verificar allowances en polymarket.com (3 contratos: CTF Exchange,
  Neg-risk CTF, Neg-risk Adapter).

### No llegan notificaciones de Telegram

- Verificar `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en `.env`.
- Hablar con el bot y mandarle `/start` (sin esto Telegram no entrega).

---

## Siguiente paso

Lee **MANUAL.md** para entender cómo opera el bot día a día.
