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

# Outbox hanya bisa dibaca pengguna fleetview atau root. Membedakan "tidak
# berhak membaca" dari "berkasnya rusak" itu penting: yang pertama hanya butuh
# sudo, yang kedua berarti data kapal dalam bahaya. Pesan yang menyamakan
# keduanya membuat teknisi panik pada kasus yang sebenarnya sepele.
OUTBOX_READABLE=""
if [ -r "${OUTBOX}" ] && sqlite3 "${OUTBOX}" "SELECT 1;" >/dev/null 2>&1; then
  OUTBOX_READABLE="ya"
fi

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
# Kemajuan diukur dari counter sequence di outbox, BUKAN dari StatusText systemd.
#
# StatusText hanya diperbarui saat heartbeat watchdog, yaitu tiap WatchdogSec/2 —
# 30 detik pada konfigurasi kita. Membandingkan dua cuplikan berjarak 3 detik
# hampir selalu melihat angka yang sama, lalu melaporkan "polling tidak maju"
# padahal akuisisi berjalan normal. Alarm palsu yang muncul hampir setiap kali
# lebih buruk daripada tidak ada alarm: teknisi berhenti mempercayainya.
#
# Counter sequence bertambah setiap kali pembacaan disimpan, jadi 3 detik sudah
# cukup untuk melihat perubahan yang sesungguhnya.
if [ -z "${OUTBOX_READABLE}" ]; then
  if [ "$(id -u)" -ne 0 ]; then
    warn "butuh akses outbox — jalankan: sudo fleetview-status"
  else
    bad "outbox tidak terbaca: ${OUTBOX}"
  fi
else
  q1="$(sqlite3 "${OUTBOX}" "SELECT COALESCE(value,0) FROM counters WHERE name='sequence';")"
  sleep 3
  q2="$(sqlite3 "${OUTBOX}" "SELECT COALESCE(value,0) FROM counters WHERE name='sequence';")"
  if [ "${q2:-0}" -gt "${q1:-0}" ]; then
    good "akuisisi maju: $(( q2 - q1 )) pembacaan dalam 3 detik"
  else
    bad "akuisisi TIDAK maju (sequence tetap ${q1:-0}) — watchdog akan merestart"
  fi
  if [ -n "${HAS_SYSTEMD}" ]; then
    st="$(systemctl show fleetview-agent -p StatusText --value 2>/dev/null)"
    [ -n "${st}" ] && printf '  %s%s\n' "${DIM}ringkasan agent: ${st}" "${R}"
  fi
fi

head "Data belum tersetor"
if [ -z "${OUTBOX_READABLE}" ]; then
  if [ "$(id -u)" -ne 0 ]; then
    warn "butuh akses outbox — jalankan: sudo fleetview-status"
  else
    bad "outbox tidak terbaca: ${OUTBOX}"
  fi
else
  sqlite3 "${OUTBOX}" \
    "SELECT '  ' || state || ': ' || SUM(record_count) || ' record' FROM outbox GROUP BY state;"
  age="$(sqlite3 "${OUTBOX}" \
    "SELECT CAST((strftime('%s','now')*1000000 - MIN(captured_at))/86400000000 AS INT)
     FROM outbox WHERE state='pending';" 2>/dev/null)"
  [ -n "${age}" ] && printf '  data pending tertua: %s hari\n' "${age}"
  q="$(sqlite3 "${OUTBOX}" "SELECT COUNT(*) FROM batches WHERE state='failed';" 2>/dev/null)"
  [ "${q:-0}" -gt 0 ] && bad "${q} batch dikarantina — lihat 09-troubleshooting.md"
fi

head "Disk"
df -Ph "${DATA_DIR}" 2>/dev/null | awk 'NR==2 {printf "  %s terpakai %s, sisa %s\n", $6, $5, $4}'
mountpoint -q "${DATA_DIR}" 2>/dev/null \
  || warn "${DATA_DIR} bukan mount terpisah — pastikan bukan SD card"

head "Setup"
PIN_FILE="${DATA_DIR}/setup.pin"
if [ -f "${PIN_FILE}" ] && [ ! -f "${DATA_DIR}/outbox.db" ]; then
  if [ -r "${PIN_FILE}" ]; then
    printf '  perangkat BELUM dikonfigurasi\n'
    printf '  PIN setup: %s%s%s\n' "${GREEN}" "$(cat "${PIN_FILE}")" "${R}"
    printf '  buka Console lalu isi formulir setup\n'
  else
    warn "PIN setup ada tetapi tidak terbaca — jalankan dengan sudo"
  fi
else
  good "perangkat sudah dikonfigurasi"
fi

head "Console"
curl -sf -o /dev/null --max-time 3 "http://127.0.0.1:${CONSOLE_PORT}/" \
  && good "http://127.0.0.1:${CONSOLE_PORT}" || warn "Console tidak merespons"

head "Kesalahan terakhir"
journalctl -u fleetview-agent -o cat -n 300 --no-pager 2>/dev/null \
  | grep '"level": *"error"' | tail -3 | cut -c1-200 || printf '  (tidak ada)\n'
printf '\n'
