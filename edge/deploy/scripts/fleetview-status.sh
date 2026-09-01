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
# Sengaja TIDAK dinamai `head`: nama itu menutupi utilitas `head`, dan setiap
# pipa `| head -1` di dalam skrip ini akan diam-diam memanggil fungsi ini
# sebagai gantinya. Gejalanya menyesatkan — nilai yang dibaca berubah menjadi
# garis judul, dan kegagalannya muncul jauh dari sebabnya.
judul() { printf '\n%s\n' "${DIM}── $* ──────────────────────────────${R}"; }
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

judul "Layanan"
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

judul "Akuisisi"
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

judul "Data belum tersetor"
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

judul "Penyimpanan lokal (InfluxDB)"
# Menjawab pertanyaan yang paling sering muncul saat memasang: "datanya
# benar-benar tersimpan atau tidak?" Tanpa ini, jawabannya menuntut query Flux
# yang tidak masuk akal untuk dihafal teknisi.
if ! curl -sf -m 5 http://127.0.0.1:8086/health >/dev/null 2>&1; then
  bad "InfluxDB tidak menjawab — pembacaan tidak tersimpan lokal"
  warn "sinkronisasi ke pusat TIDAK terpengaruh; outbox tetap menampung"
elif [ -r "${CONFIG_DIR:-/etc/fleetview}/agent.env" ]; then
  INFLUX_TOKEN="$(sed -n 's/^FLEETVIEW_STORAGE__INFLUX_TOKEN=//p' \
    "${CONFIG_DIR:-/etc/fleetview}/agent.env" | head -1)"
  if [ -z "${INFLUX_TOKEN}" ]; then
    bad "token InfluxDB belum disetel — setiap penulisan akan ditolak 401"
  else
    # Keluaran CSV InfluxDB memakai CRLF dan diawali dua kolom kosong, sehingga
    # nilainya ada di kolom TERAKHIR — bukan pada nomor kolom tetap, yang
    # berubah mengikuti bentuk query. Baris datanya dikenali dari awalan ",,".
    titik="$(influx query --host http://127.0.0.1:8086 --token "${INFLUX_TOKEN}" \
      --org "${FLEETVIEW_INFLUX_ORG:-fleetview}" --raw \
      'from(bucket:"'"${FLEETVIEW_INFLUX_BUCKET:-telemetry}"'")
         |> range(start:-10m)
         |> filter(fn:(r) => r._field == "value")
         |> count() |> group() |> sum()' 2>/dev/null \
      | awk -F, '/^,,/ {gsub(/\r/, ""); nilai=$NF} END {print nilai+0}')"
    if [ -n "${titik}" ] && [ "${titik}" -gt 0 ] 2>/dev/null; then
      good "${titik} pembacaan tersimpan dalam 10 menit terakhir"
    else
      bad "tidak ada pembacaan tersimpan dalam 10 menit terakhir"
      warn "periksa 'journalctl -u fleetview-agent | grep storage'"
    fi
  fi
else
  warn "butuh akses agent.env — jalankan dengan sudo"
fi

judul "Disk"
df -Ph "${DATA_DIR}" 2>/dev/null | awk 'NR==2 {printf "  %s terpakai %s, sisa %s\n", $6, $5, $4}'
# Jenis medianya disebutkan, bukan sekadar "pastikan bukan SD card". Menyuruh
# teknisi memastikan sesuatu yang bisa diperiksa program adalah pekerjaan yang
# tidak perlu — dan yang paling sering dilewati.
_media() {
  local dev jenis
  dev="$(findmnt -no SOURCE "$1" 2>/dev/null || findmnt -no SOURCE / 2>/dev/null)"
  dev="$(printf '%s' "${dev}" | sed 's|/dev/||;s/[0-9]*$//;s/p$//')"
  case "${dev}" in
    nvme*)   jenis="NVMe" ;;
    mmcblk*) jenis="kartu SD" ;;
    *)       [ "$(cat "/sys/block/${dev}/queue/rotational" 2>/dev/null)" = "1" ] \
               && jenis="HDD" || jenis="SSD" ;;
  esac
  printf '%s|%s' "${dev}" "${jenis}"
}
IFS='|' read -r _dev _jenis <<EOF
$(_media "${DATA_DIR}")
EOF
if mountpoint -q "${DATA_DIR}" 2>/dev/null; then
  case "${_jenis}" in
    "kartu SD") bad "data di kartu SD (${_dev}) — akan habis dalam hitungan bulan" ;;
    HDD)        warn "data di HDD (${_dev}) — getaran kapal memperpendek umurnya" ;;
    *)          good "data di ${_jenis} (${_dev})" ;;
  esac
else
  if [ "${_jenis}" = "kartu SD" ]; then
    bad "${DATA_DIR} bukan mount terpisah — data menulis ke kartu SD (${_dev})"
    warn "jalankan: sudo fleetview-storage"
  else
    warn "${DATA_DIR} bukan mount terpisah — data ikut di ${_dev} (${_jenis})"
  fi
fi

judul "Setup"
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

judul "Console"
curl -sf -o /dev/null --max-time 3 "http://127.0.0.1:${CONSOLE_PORT}/" \
  && good "http://127.0.0.1:${CONSOLE_PORT}" || warn "Console tidak merespons"

judul "Kesalahan terakhir"
journalctl -u fleetview-agent -o cat -n 300 --no-pager 2>/dev/null \
  | grep '"level": *"error"' | tail -3 | cut -c1-200 || printf '  (tidak ada)\n'
printf '\n'
