#!/usr/bin/env bash
# Peringatan dini ruang disk.
#
# Dua ambang, bukan satu. Ambang pertama memberi waktu untuk bertindak; ambang
# kedua adalah keadaan darurat. Alert bernada sama untuk keduanya akan membuat
# yang pertama diabaikan, dan alert yang diabaikan sama saja dengan tidak ada.
set -euo pipefail

WARN_PCT="${FLEETVIEW_DISK_WARN_PCT:-75}"
CRIT_PCT="${FLEETVIEW_DISK_CRIT_PCT:-90}"
PATHS="${FLEETVIEW_DISK_PATHS:-/var/lib/influxdb /var/lib/postgresql /var/backups/fleetview}"

status=0
for path in ${PATHS}; do
  [[ -d "${path}" ]] || continue
  used="$(df -P "${path}" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
  avail="$(df -Ph "${path}" | awk 'NR==2 {print $4}')"
  if (( used >= CRIT_PCT )); then
    logger -t fleetview-diskwatch -p daemon.crit \
      "KRITIS: ${path} terpakai ${used}%, sisa ${avail}. Ingest seluruh armada terancam berhenti."
    status=2
  elif (( used >= WARN_PCT )); then
    logger -t fleetview-diskwatch -p daemon.warning \
      "PERINGATAN: ${path} terpakai ${used}%, sisa ${avail}."
    [[ ${status} -lt 1 ]] && status=1
  fi
done
exit "${status}"
