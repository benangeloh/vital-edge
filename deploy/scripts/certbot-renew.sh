#!/usr/bin/env bash
# Renewal TLS periodik — dipicu fleetview-certbot-renew.timer.
set -euo pipefail

COMPOSE_FILE="${FLEETVIEW_COMPOSE_FILE:-/opt/fleetview/src/docker-compose.prod.yml}"

docker compose -f "${COMPOSE_FILE}" run --rm certbot renew --quiet
docker compose -f "${COMPOSE_FILE}" exec proxy nginx -s reload
