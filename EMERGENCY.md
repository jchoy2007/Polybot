# EMERGENCY — Polybot sin Claude

Instrucciones para manejar el bot cuando Claude no esté disponible.

## Diagnóstico rápido

```bash
ssh root@TU_IP
cd /root/Polybot
systemctl status polybot
./venv/bin/python scripts/daily_audit.py
```

## Problemas comunes

### Bot no corre

```bash
systemctl restart polybot
systemctl status polybot
```

### Balance bajando feo

```bash
./venv/bin/python scripts/daily_audit.py
```

Si balance menor a $60: `systemctl stop polybot`

### Cobros atascados

```bash
./venv/bin/python redeem.py
```

### Errores en logs

```bash
tail -200 logs/polybot_*.log | grep -i error | tail -20
```

### Pausar de emergencia

```bash
systemctl stop polybot
```

## Rollback

```bash
git -C /root/Polybot log --oneline -20
git -C /root/Polybot revert HEAD
git -C /root/Polybot push origin main
systemctl restart polybot
```

## Retirar capital

1. `systemctl stop polybot`
2. Entrar a polymarket.com con wallet `0x4bcd69...`
3. Retirar USDC.e a MetaMask
4. Pasar a exchange o guardar

## Enlaces

- Repo: https://github.com/jchoy2007/Polybot
- Hetzner: https://console.hetzner.cloud
- Polymarket: https://polymarket.com
