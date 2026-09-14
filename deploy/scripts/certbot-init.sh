#!/usr/bin/env bash
# Setup sertifikat TLS awal untuk Central Platform (sekali jalan, manual).
set -euo pipefail

DOMAIN="${1:?Pemakaian: certbot-init.sh <domain> <email>}"
EMAIL="${2:?Pemakaian: certbot-init.sh <domain> <email>}"
COMPOSE_FILE="${FLEETVIEW_COMPOSE_FILE:-/opt/fleetview/src/docker-compose.prod.yml}"

sed -i "s/server_name _;/server_name ${DOMAIN};/g" \
  /opt/fleetview/src/central/web/nginx/fleetview.conf

docker compose -f "${COMPOSE_FILE}" up -d proxy

docker compose -f "${COMPOSE_FILE}" run --rm certbot \
  certonly --webroot -w /var/www/acme-challenge \
  -d "${DOMAIN}" --email "${EMAIL}" --agree-tos --non-interactive \
  --cert-name fleetview

docker compose -f "${COMPOSE_FILE}" exec proxy nginx -s reload
