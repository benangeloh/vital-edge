#!/usr/bin/env bash
# Test klasifikasi media untuk detect-storage.sh, tanpa menyentuh disk sungguhan.
#
# Skrip produksinya tidak diuji lewat pytest karena isinya interaksi langsung
# dengan /sys dan /dev — mengekstrak fungsi klasifikasinya ke sini menjaga
# logikanya tetap sama persis dengan yang berjalan di kapal.
set -euo pipefail

klasifikasi() {
  local n="$1" rota="$2" tran="$3"
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

gagal=0
uji() {
  local nama="$1" perangkat="$2" rota="$3" tran="$4" harap="$5"
  local dapat
  dapat="$(klasifikasi "$perangkat" "$rota" "$tran")"
  if [ "$dapat" = "$harap" ]; then
    echo "  ok    $nama"
  else
    echo "  GAGAL $nama: harap=$harap dapat=$dapat"
    gagal=1
  fi
}

uji "NVMe HAT"                    nvme0n1 0 nvme  nvme
uji "microSD"                     mmcblk0 0 mmc    sdcard
uji "SSD SATA internal"           sda     0 sata    ssd
uji "SSD lewat adapter USB"       sdb     0 usb     ssd-usb
uji "HDD SATA internal"           sdc     1 sata    hdd
uji "HDD lewat enclosure USB"     sdd     1 usb     hdd
uji "beberapa NVMe, yang kedua"   nvme1n1 0 nvme    nvme

[ "$gagal" -eq 0 ] && echo "semua uji klasifikasi lulus" || exit 1
