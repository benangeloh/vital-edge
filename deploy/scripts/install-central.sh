#!/usr/bin/env bash
# Setup awal server produksi Central Platform — sekali jalan.
# Prasyarat: Docker + Docker Compose plugin sudah terpasang di server, dan
# script ini dijalankan dari root checkout repo (direktori yang sama dengan
# docker-compose.prod.yml dan .env.prod.example).
set -euo pipefail

DOMAIN="${1:?Pemakaian: install-central.sh <domain> <email>}"
EMAIL="${2:?Pemakaian: install-central.sh <domain> <email>}"
DATA_DIR="${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}"

sudo mkdir -p "${DATA_DIR}/postgres-data" "${DATA_DIR}/influx-data"
# uid 70 = postgres di image postgres:16-alpine, uid 1000 = influxdb di
# image influxdb:2.7-alpine (bukan root, image resmi berjalan non-root).
sudo chown -R 70:70 "${DATA_DIR}/postgres-data"
sudo chown -R 1000:1000 "${DATA_DIR}/influx-data"

sudo install -d -m 0750 /etc/fleetview/secrets

# .env di sini (bukan /etc/fleetview/central.env) — file ini dibaca Docker
# Compose sendiri untuk interpolasi ${VAR} di docker-compose.prod.yml
# (COMPOSE_PROJECT_NAME, FLEETVIEW_DATA_DIR, POSTGRES_USER, dst.), dan Compose
# hanya mencarinya di direktori yang sama dengan file compose-nya — bukan lewat
# `env_file:` layanan mana pun. Ini terpisah total dari
# /etc/fleetview/central.env (konfigurasi aplikasi FastAPI), yang TIDAK dibuat
# otomatis di sini karena isinya nilai produksi nyata yang harus ditulis
# tangan oleh operator (lihat pesan di akhir script).
[[ -f .env ]] || cp .env.prod.example .env

if [[ ! -f /etc/fleetview/secrets/postgres_password.txt ]]; then
  openssl rand -hex 16 | sudo tee /etc/fleetview/secrets/postgres_password.txt >/dev/null
fi
if [[ ! -f /etc/fleetview/secrets/influx_password.txt ]]; then
  openssl rand -hex 16 | sudo tee /etc/fleetview/secrets/influx_password.txt >/dev/null
fi
if [[ ! -f /etc/fleetview/secrets/influx_token.txt ]]; then
  openssl rand -hex 32 | sudo tee /etc/fleetview/secrets/influx_token.txt >/dev/null
fi
sudo chmod 0600 /etc/fleetview/secrets/*.txt

echo "Berikutnya (manual, sekali):"
echo "1. Buat /etc/fleetview/central.env dan /etc/fleetview/secrets.env dengan"
echo "   tangan — belum dibuat otomatis oleh script ini. Lihat"
echo "   docs/operations/03-central-deployment.md untuk format dan daftar"
echo "   variabelnya (FLEETVIEW_ENVIRONMENT, FLEETVIEW_HOST,"
echo "   FLEETVIEW_POSTGRES_DSN, FLEETVIEW_INFLUX_TOKEN, FLEETVIEW_JWT_SECRET,"
echo "   dst.) — nilai token/password harus SAMA dengan isi"
echo "   /etc/fleetview/secrets/*.txt di atas."
echo "2. docker compose -f docker-compose.prod.yml up -d postgres influxdb migrate api"
echo "3. ./deploy/scripts/certbot-init.sh ${DOMAIN} ${EMAIL}"
echo "4. sudo cp deploy/systemd/fleetview-certbot-renew.* /etc/systemd/system/"
echo "   sudo systemctl enable --now fleetview-certbot-renew.timer"
