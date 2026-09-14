#!/usr/bin/env bash
# Setup sertifikat TLS awal untuk Central Platform (sekali jalan, manual).
set -euo pipefail

DOMAIN="${1:?Pemakaian: certbot-init.sh <domain> <email>}"
EMAIL="${2:?Pemakaian: certbot-init.sh <domain> <email>}"
COMPOSE_FILE="${FLEETVIEW_COMPOSE_FILE:-/opt/fleetview/src/docker-compose.prod.yml}"
CERT_DIR="${FLEETVIEW_CERTS_DIR:-/etc/letsencrypt}/live/fleetview"

# nginx menolak start SAMA SEKALI bila berkas sertifikat yang di-hardcode di
# fleetview.conf tidak ada -- termasuk saat pertama kali server ini disiapkan,
# sebelum certbot sempat menerbitkan sertifikat asli. Tanpa placeholder ini
# proxy tidak pernah naik, port 80 tidak pernah terbuka, dan ACME HTTP-01
# challenge di bawah tidak pernah bisa dijawab -- deadlock. Hanya dibuat bila
# sertifikat asli belum ada; certbot menimpanya di langkah berikutnya, jadi
# menjalankan ulang script ini di host yang sudah punya sertifikat valid
# membuat blok ini no-op.
if [[ ! -s "${CERT_DIR}/fullchain.pem" ]]; then
  mkdir -p "${CERT_DIR}"
  openssl req -x509 -nodes -days 1 -newkey rsa:2048 \
    -keyout "${CERT_DIR}/privkey.pem" \
    -out "${CERT_DIR}/fullchain.pem" \
    -subj "/CN=${DOMAIN}"
fi

docker compose -f "${COMPOSE_FILE}" up -d proxy

docker compose -f "${COMPOSE_FILE}" run --rm certbot \
  certonly --webroot -w /var/www/acme-challenge \
  -d "${DOMAIN}" --email "${EMAIL}" --agree-tos --non-interactive \
  --cert-name fleetview

docker compose -f "${COMPOSE_FILE}" exec proxy nginx -s reload
