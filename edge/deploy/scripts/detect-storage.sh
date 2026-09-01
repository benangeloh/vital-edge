#!/usr/bin/env bash
# Kenali media penyimpanan perangkat dan nilai kecocokannya untuk data FleetView.
#
#   detect-storage.sh              periksa dan laporkan (tidak mengubah apa pun)
#   detect-storage.sh --siapkan /dev/sda   format & pasang permanen  <- MERUSAK
#
# Kenapa ada: 70 kapal tidak akan seragam. Ada yang NVMe, ada SSD USB, ada HDD,
# ada yang cuma microSD. Menyerahkan pilihan itu ke ingatan teknisi berarti
# sebagian kapal akan berakhir menulis 86.400 fsync per hari ke kartu SD, dan
# kegagalannya baru muncul berbulan-bulan kemudian sebagai perangkat yang mati
# total di tengah laut.
#
# Yang TIDAK dilakukan skrip ini: memformat sendiri. Deteksi otomatis boleh
# salah menebak; memformat tidak bisa dibatalkan. Format hanya terjadi bila
# perangkatnya disebut eksplisit lewat --siapkan, dan setelah dikonfirmasi.
set -uo pipefail

DATA_DIR="${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}"

if [ -t 1 ]; then
  R=$'\033[0m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'
else
  R=""; DIM=""; RED=""; GREEN=""; YEL=""
fi

# --- klasifikasi -------------------------------------------------------------

klasifikasi() {
  # $1 = nama perangkat (mis. sda, nvme0n1, mmcblk0)
  local n="$1" rota tran
  rota="$(cat "/sys/block/$n/queue/rotational" 2>/dev/null || echo 0)"
  tran="$(lsblk -dno TRAN "/dev/$n" 2>/dev/null | tr -d ' ')"

  case "$n" in
    nvme*)    echo "nvme" ;;
    mmcblk*)  echo "sdcard" ;;
    *)
      if [ "$rota" = "1" ]; then echo "hdd"
      elif [ "$tran" = "usb" ]; then echo "ssd-usb"
      else echo "ssd"
      fi ;;
  esac
}

nilai() {
  # Urutan ini mengikuti apa yang menentukan umur perangkat di kapal, bukan
  # kecepatannya: ketahanan tulis, lalu ketahanan getaran.
  case "$1" in
    nvme)    echo "3|${GREEN}sangat cocok${R}|paling andal; Pi 5 punya slot PCIe" ;;
    ssd)     echo "3|${GREEN}sangat cocok${R}|tanpa bagian bergerak" ;;
    ssd-usb) echo "2|${GREEN}cocok${R}|hindari adapter USB-SATA murah (masalah UAS)" ;;
    hdd)     echo "1|${YEL}kurang cocok${R}|bagian bergerak; getaran mesin & poros memperpendek umurnya" ;;
    sdcard)  echo "0|${RED}jangan untuk data${R}|fsync tiap detik menghabiskannya dalam hitungan bulan" ;;
    *)       echo "0|tidak dikenal|periksa manual" ;;
  esac
}

# --- pemeriksaan -------------------------------------------------------------

akar="$(findmnt -no SOURCE / 2>/dev/null | sed 's/[0-9]*$//;s|/dev/||;s|p$||')"
terbaik_dev=""; terbaik_skor=-1; terbaik_jenis=""

printf '\n%s\n' "${DIM}── Media penyimpanan yang terdeteksi ──────────────${R}"
ditemukan=0
for d in /sys/block/*/; do
  n="$(basename "$d")"
  case "$n" in loop*|ram*|zram*|dm-*|sr*) continue;; esac
  ditemukan=$((ditemukan + 1))

  jenis="$(klasifikasi "$n")"
  IFS='|' read -r skor label catatan <<EOF
$(nilai "$jenis")
EOF
  ukuran="$(lsblk -dno SIZE "/dev/$n" 2>/dev/null | tr -d ' ')"
  model="$(lsblk -dno MODEL "/dev/$n" 2>/dev/null | sed 's/ *$//')"
  tanda=""
  [ "$n" = "$akar" ] && tanda=" ${DIM}(berisi sistem operasi)${R}"

  printf '  %-10s %-8s %-9s %s%s\n' "$n" "$ukuran" "$jenis" "$label" "$tanda"
  printf '             %s%s%s\n' "${DIM}" "$catatan" "${R}"

  # Kandidat data: bukan perangkat yang memuat OS, dan skornya lebih baik.
  if [ "$n" != "$akar" ] && [ "$skor" -gt "$terbaik_skor" ]; then
    terbaik_dev="$n"; terbaik_skor="$skor"; terbaik_jenis="$jenis"
  fi
done
[ "$ditemukan" -eq 0 ] && printf '  tidak ada perangkat blok terdeteksi\n'

printf '\n%s\n' "${DIM}── Tempat data FleetView sekarang ─────────────────${R}"
if mountpoint -q "${DATA_DIR}" 2>/dev/null; then
  sumber="$(findmnt -no SOURCE "${DATA_DIR}")"
  induk="$(echo "$sumber" | sed 's|/dev/||;s/[0-9]*$//;s/p$//')"
  jenis="$(klasifikasi "$induk")"
  IFS='|' read -r _s label catatan <<EOF
$(nilai "$jenis")
EOF
  printf '  %s  ->  %s (%s) %s\n' "${DATA_DIR}" "$sumber" "$jenis" "$label"
  printf '  %s%s%s\n' "${DIM}" "$catatan" "${R}"
else
  jenis_akar="$(klasifikasi "${akar:-x}")"
  printf '  %s%s bukan mount terpisah — data ikut di %s (%s)%s\n' \
    "${RED}" "${DATA_DIR}" "${akar:-?}" "$jenis_akar" "${R}"
  if [ "$jenis_akar" = "sdcard" ]; then
    printf '  %sSeluruh telemetry menulis ke kartu SD. Ini penyebab kegagalan\n' "${RED}"
    printf '  paling sering pada sistem sejenis.%s\n' "${R}"
  fi
fi

if [ -n "$terbaik_dev" ]; then
  printf '\n%s\n' "${DIM}── Saran ──────────────────────────────────────────${R}"
  printf '  Pindahkan data ke %s%s%s (%s):\n\n' "${GREEN}" "$terbaik_dev" "${R}" "$terbaik_jenis"
  printf '    sudo %s --siapkan /dev/%s\n\n' "$0" "$terbaik_dev"
  printf '  %sIsi perangkat itu akan DIHAPUS. Periksa dulu bahwa ia benar kosong.%s\n' \
    "${YEL}" "${R}"
fi
printf '\n'

# --- penyiapan (merusak, hanya bila diminta eksplisit) ------------------------

[ "${1:-}" = "--siapkan" ] || exit 0
target="${2:-}"
[ -n "$target" ] || { echo "pakai: $0 --siapkan /dev/sdX" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "butuh root" >&2; exit 1; }
[ -b "$target" ] || { echo "bukan perangkat blok: $target" >&2; exit 1; }

nama_t="$(basename "$target" | sed 's/[0-9]*$//;s/p$//')"
if [ "$nama_t" = "$akar" ]; then
  echo "MENOLAK: $target memuat sistem operasi." >&2
  exit 1
fi
if findmnt -S "$target" >/dev/null 2>&1 || lsblk -no MOUNTPOINT "$target" 2>/dev/null | grep -q .; then
  echo "MENOLAK: $target sedang ter-mount. Lepaskan dulu." >&2
  exit 1
fi

echo
echo "AKAN MENGHAPUS SELURUH ISI $target:"
lsblk "$target" 2>/dev/null | sed 's/^/  /'
echo
printf 'Ketik nama perangkatnya untuk melanjutkan (%s): ' "$target"
read -r jawab
[ "$jawab" = "$target" ] || { echo "dibatalkan"; exit 1; }

echo "==> memformat ext4"
mkfs.ext4 -q -L fleetview-data "$target"
uuid="$(blkid -s UUID -o value "$target")"
[ -n "$uuid" ] || { echo "gagal membaca UUID" >&2; exit 1; }

# Dipasang berdasarkan UUID, bukan /dev/sda1: urutan penamaan perangkat USB bisa
# berubah setelah reboot, dan salah mount berarti agent menulis ke kartu SD
# tanpa ada yang menyadarinya.
sed -i "\|${DATA_DIR}|d" /etc/fstab
echo "UUID=${uuid} ${DATA_DIR} ext4 defaults,noatime 0 2" >> /etc/fstab

lama=""
if [ -d "${DATA_DIR}" ] && [ -n "$(ls -A "${DATA_DIR}" 2>/dev/null)" ]; then
  lama="$(mktemp -d)"
  cp -a "${DATA_DIR}/." "$lama/"
  echo "==> isi lama disalin sementara"
fi

mkdir -p "${DATA_DIR}"
mount "${DATA_DIR}"
if [ -n "$lama" ]; then
  cp -a "$lama/." "${DATA_DIR}/"
  rm -rf "$lama"
  echo "==> isi lama dikembalikan"
fi
chown fleetview:fleetview "${DATA_DIR}" 2>/dev/null || true
chmod 0750 "${DATA_DIR}"

echo
findmnt "${DATA_DIR}" | sed 's/^/  /'
echo
echo "Selesai. Jalankan ulang agent:  systemctl restart fleetview-agent"
