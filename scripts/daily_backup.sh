#!/usr/bin/env bash
# daily_backup.sh — respalda data/*.json a backups/YYYY-MM-DD/
# y limita a los últimos 7 días. Diseñado para cron diario.

set -euo pipefail

PROJECT_ROOT="/root/Polybot"
SRC="${PROJECT_ROOT}/data"
BACKUP_ROOT="${PROJECT_ROOT}/backups"
TODAY="$(date -u +%Y-%m-%d)"
DEST="${BACKUP_ROOT}/${TODAY}"

mkdir -p "${DEST}"

# Copiar todos los .json del dir data/. -u solo actualiza si cambió.
if compgen -G "${SRC}/*.json" > /dev/null; then
    cp -u "${SRC}"/*.json "${DEST}/"
fi

# Rotación: mantener últimos 7 directorios (ordenados lexicográficamente
# por YYYY-MM-DD). Borra los más viejos.
if [ -d "${BACKUP_ROOT}" ]; then
    ls -1 "${BACKUP_ROOT}" 2>/dev/null \
        | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' \
        | sort \
        | head -n -7 \
        | while read -r old_dir; do
            rm -rf "${BACKUP_ROOT:?}/${old_dir}"
        done
fi

echo "$(date -u +%FT%TZ) backup OK → ${DEST}"
