#!/usr/bin/env bash
# Cadangan central: PostgreSQL dan InfluxDB.
#
# Keduanya punya nilai yang berbeda dan itu memengaruhi caranya:
#   - PostgreSQL menyimpan identitas kapal, kredensial perangkat, dan LEDGER
#     BATCH. Ledger itu yang membuat pengiriman ulang tidak menggandakan data.
#     Kehilangannya tidak menghilangkan telemetry, tetapi membuat kapal yang
#     mengirim ulang backlog-nya menghasilkan duplikat. Ukurannya kecil, jadi
#     dicadangkan penuh setiap hari.
#   - InfluxDB menyimpan telemetry. Besar, dan sebagian besar isinya masih ada
#     di kapal selama masa tenggang outbox. Dicadangkan dengan cara inkremental.
set -euo pipefail

BACKUP_DIR="${FLEETVIEW_BACKUP_DIR:-/var/backups/fleetview}"
KEEP_DAYS="${FLEETVIEW_BACKUP_KEEP_DAYS:-30}"
PGDATABASE="${POSTGRES_DB:-fleetview}"
PGUSER="${POSTGRES_USER:-fleetview}"
INFLUX_BUCKET="${INFLUXDB_BUCKET:-telemetry}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="${BACKUP_DIR}/${stamp}"
mkdir -p "${dest}"

# --- PostgreSQL -------------------------------------------------------------
# Format custom (-Fc), bukan SQL polos: bisa dipulihkan sebagian (hanya satu
# tabel, misalnya) dan terkompresi. Saat pemulihan darurat, memulihkan hanya
# tabel ledger jauh lebih cepat daripada seluruh basis data.
pg_dump -Fc -U "${PGUSER}" -d "${PGDATABASE}" -f "${dest}/postgres.dump"

# Verifikasi isinya sekarang. pg_dump bisa sukses menghasilkan berkas yang tidak
# bisa dibaca kembali bila disk penuh di tengah jalan.
pg_restore --list "${dest}/postgres.dump" > /dev/null

# --- InfluxDB ---------------------------------------------------------------
# Inkremental: hanya 48 jam terakhir. Cadangan penuh telemetry armada berukuran
# ratusan GB dan tidak praktis harian. Data yang lebih lama dilindungi oleh
# snapshot mingguan (lihat docs/operations/04-backup.md) dan, untuk jendela
# terbaru, oleh outbox di kapal yang belum dipangkas.
if command -v influx >/dev/null 2>&1; then
  influx backup "${dest}/influx" \
    --bucket "${INFLUX_BUCKET}" \
    --start "$(date -u -d '48 hours ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
              || date -u -v-48H +%Y-%m-%dT%H:%M:%SZ)" \
    >/dev/null
fi

sha256sum "${dest}/postgres.dump" > "${dest}/SHA256SUMS"
du -sh "${dest}" | awk '{print "cadangan selesai: " $2 " (" $1 ")"}'

# Pemangkasan dilakukan setelah cadangan baru terverifikasi.
find "${BACKUP_DIR}" -maxdepth 1 -mindepth 1 -type d -mtime "+${KEEP_DAYS}" -exec rm -rf {} +
