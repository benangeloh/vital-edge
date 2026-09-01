#!/usr/bin/env bash
# Ringkasan kondisi satu kapal dalam satu perintah.
#
# Menggantikan empat perintah pertama di docs/operations/09-troubleshooting.md.
# Dipasang sebagai /usr/local/bin/fleetview-status oleh install.sh, supaya
# teknisi yang naik ke kapal cukup mengetik satu hal.
#
# TIDAK menghentikan apa pun dan tidak menulis apa pun. Selama agent berjalan,
# data terus terkumpul — dan agent yang dihentikan "sebentar" untuk diperiksa
# adalah penyebab kehilangan data yang paling sering terjadi.
set -uo pipefail

DATA_DIR="${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}"
OUTBOX="${DATA_DIR}/outbox.db"
CONSOLE_PORT="${FLEETVIEW_CONSOLE_PORT:-8080}"

if [ -t 1 ]; then
  R=$'\033[0m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'
else
  R=""; DIM=""; RED=""; GREEN=""; YEL=""
fi
head() { printf '\n%s\n' "${DIM}── $* ──────────────────────────────${R}"; }
good() { printf '  %sok%s   %s\n' "${GREEN}" "${R}" "$*"; }
bad()  { printf '  %s!!%s   %s\n' "${RED}" "${R}" "$*"; }
warn() { printf '  %s!%s    %s\n' "${YEL}" "${R}" "$*"; }

head "Layanan"
# Skrip ini juga kadang dijalankan di luar systemd — saat pengembangan, atau di
# dalam kontainer. Tanpa penjaga ini, `systemctl: command not found` akan
# terbaca seolah agent-nya yang bermasalah.
if ! command -v systemctl >/dev/null 2>&1; then
  warn "systemd tidak tersedia di mesin ini; bagian layanan dan akuisisi dilewati"
  HAS_SYSTEMD=""
else
  HAS_SYSTEMD="ya"
fi

if [ -z "${HAS_SYSTEMD}" ]; then
  :
elif systemctl is-active --quiet fleetview-agent; then
  good "agent aktif sejak $(systemctl show fleetview-agent -p ActiveEnterTimestamp --value)"
  printf '       %s\n' "$(systemctl show fleetview-agent -p StatusText --value)"
  restarts="$(systemctl show fleetview-agent -p NRestarts --value)"
  [ "${restarts:-0}" -gt 0 ] && warn "sudah direstart ${restarts}x sejak boot"
else
  bad "agent TIDAK aktif — kapal sedang tidak mengumpulkan data"
  systemctl is-enabled --quiet fleetview-agent || bad "dan tidak akan hidup saat reboot (disabled)"
fi
if [ -n "${HAS_SYSTEMD}" ]; then
  systemctl is-active --quiet influxdb && good "influxdb aktif" \
    || warn "influxdb mati — akuisisi tetap jalan, penyimpanan lokal tertunda"
fi

head "Akuisisi"
if [ -z "${HAS_SYSTEMD}" ]; then
  warn "butuh systemd"
else
# Dua cuplikan berjarak: satu angka poll tidak memberi tahu apa pun. Yang
# menentukan adalah apakah ia BERTAMBAH.
s1="$(systemctl show fleetview-agent -p StatusText --value 2>/dev/null)"
sleep 3
s2="$(systemctl show fleetview-agent -p StatusText --value 2>/dev/null)"
p1="$(printf '%s' "${s1}" | sed -n 's/.*polls=\([0-9]*\).*/\1/p')"
p2="$(printf '%s' "${s2}" | sed -n 's/.*polls=\([0-9]*\).*/\1/p')"
if [ -n "${p1}" ] && [ -n "${p2}" ]; then
  if [ "${p2}" -gt "${p1}" ]; then
    good "polling maju: ${p1} -> ${p2}"
  else
    bad "polling TIDAK maju (tetap ${p1}) — watchdog akan merestart"
  fi
else
  warn "status akuisisi belum tersedia (agent baru mulai?)"
fi
fi

head "Data belum tersetor"
if [ -r "${OUTBOX}" ]; then
  sqlite3 "${OUTBOX}" \
    "SELECT '  ' || state || ': ' || SUM(record_count) || ' record' FROM outbox GROUP BY state;"
  age="$(sqlite3 "${OUTBOX}" \
    "SELECT CAST((strftime('%s','now')*1000000 - MIN(captured_at))/86400000000 AS INT)
     FROM outbox WHERE state='pending';" 2>/dev/null)"
  [ -n "${age}" ] && [ "${age}" != "" ] && printf '  data pending tertua: %s hari\n' "${age}"
  q="$(sqlite3 "${OUTBOX}" "SELECT COUNT(*) FROM batches WHERE state='failed';" 2>/dev/null)"
  [ "${q:-0}" -gt 0 ] && bad "${q} batch dikarantina — lihat 09-troubleshooting.md"
else
  bad "outbox tidak terbaca: ${OUTBOX}"
fi

head "Disk"
df -Ph "${DATA_DIR}" 2>/dev/null | awk 'NR==2 {printf "  %s terpakai %s, sisa %s\n", $6, $5, $4}'
mountpoint -q "${DATA_DIR}" 2>/dev/null \
  || warn "${DATA_DIR} bukan mount terpisah — pastikan bukan SD card"

head "Console"
curl -sf -o /dev/null --max-time 3 "http://127.0.0.1:${CONSOLE_PORT}/" \
  && good "http://127.0.0.1:${CONSOLE_PORT}" || warn "Console tidak merespons"

head "Kesalahan terakhir"
journalctl -u fleetview-agent -o cat -n 300 --no-pager 2>/dev/null \
  | grep '"level": *"error"' | tail -3 | cut -c1-200 || printf '  (tidak ada)\n'
printf '\n'
