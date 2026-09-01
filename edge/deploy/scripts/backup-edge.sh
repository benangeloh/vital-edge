#!/usr/bin/env bash
# Cadangan lokal untuk satu kapal.
#
# Yang dicadangkan hanya yang tidak tergantikan:
#   - outbox.db  : data yang BELUM ter-ACK. Setelah ACK, central punya salinannya.
#   - config     : identitas kapal dan pemetaan sensor.
# InfluxDB lokal TIDAK dicadangkan. Isinya bisa dibangun ulang dari central, dan
# ukurannya membuat pencadangan harian tidak praktis di SD card.
set -euo pipefail

DATA_DIR="${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}"
CONFIG_DIR="${FLEETVIEW_CONFIG_DIR:-/etc/fleetview}"
BACKUP_DIR="${FLEETVIEW_BACKUP_DIR:-/var/lib/fleetview/backups}"
KEEP="${FLEETVIEW_BACKUP_KEEP:-7}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="${BACKUP_DIR}/${stamp}"
mkdir -p "${dest}"

# .backup, bukan cp. Menyalin berkas SQLite yang sedang ditulis menghasilkan
# berkas yang rusak atau setengah jadi; .backup mengambil snapshot konsisten
# tanpa menghentikan agent.
if [[ -f "${DATA_DIR}/outbox.db" ]]; then
  sqlite3 "${DATA_DIR}/outbox.db" ".backup '${dest}/outbox.db'"
  # Verifikasi sekarang, bukan saat pemulihan. Cadangan yang rusak dan tidak
  # diketahui lebih buruk daripada tidak punya cadangan sama sekali, karena ia
  # menciptakan rasa aman yang keliru.
  if ! sqlite3 "${dest}/outbox.db" "PRAGMA integrity_check;" | grep -qx "ok"; then
    echo "GAGAL: cadangan outbox tidak lolos integrity_check" >&2
    rm -rf "${dest}"
    exit 1
  fi
  pending="$(sqlite3 "${dest}/outbox.db" \
    "SELECT COALESCE(SUM(record_count),0) FROM outbox WHERE state='pending';")"
  # Buang WAL dan shared-memory yang tertinggal dari proses .backup. Isinya
  # sudah masuk ke berkas utama; meninggalkannya di sisi cadangan hanya membuat
  # pemulihan ambigu — SQLite akan memutar ulang WAL yang tidak lagi relevan.
  sqlite3 "${dest}/outbox.db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
  rm -f "${dest}/outbox.db-wal" "${dest}/outbox.db-shm"
  echo "outbox tercadangkan, ${pending} record belum tersetor"
fi

# --exclude WAJIB mendahului operand path. Ditaruh sesudahnya, BSD tar
# mengabaikannya sepenuhnya dan secrets.env ikut tercadangkan — kebocoran yang
# justru ingin dicegah baris ini, dan yang tidak akan terlihat tanpa memeriksa
# isi arsipnya.
tar --exclude='secrets.env' -czf "${dest}/config.tar.gz" \
  -C "$(dirname "${CONFIG_DIR}")" "$(basename "${CONFIG_DIR}")"

# Verifikasi, jangan percaya. Kalau rahasianya tetap masuk, cadangan ini dibuang
# ketimbang disimpan sebagai salinan kredensial yang tidak diawasi.
if tar -tzf "${dest}/config.tar.gz" | grep -q 'secrets\.env'; then
  echo "GAGAL: secrets.env ikut masuk ke cadangan config" >&2
  rm -rf "${dest}"
  exit 1
fi

sha256sum "${dest}"/* > "${dest}/SHA256SUMS"

# Pangkas yang lama. Pemangkasan dilakukan SETELAH cadangan baru terverifikasi,
# supaya kegagalan tidak pernah meninggalkan kapal tanpa cadangan sama sekali.
find "${BACKUP_DIR}" -maxdepth 1 -mindepth 1 -type d \
  | sort -r | tail -n +"$((KEEP + 1))" \
  | while IFS= read -r dir; do
      [ -n "${dir}" ] && rm -rf "${dir}"
    done

echo "cadangan selesai: ${dest}"
