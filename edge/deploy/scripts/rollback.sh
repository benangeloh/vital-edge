#!/usr/bin/env bash
# Kembali ke rilis sebelumnya.
#
# Rollback hanya menukar symlink. Data tidak disentuh: outbox dan InfluxDB tetap
# di tempatnya, dan nomor sequence tetap berlanjut — sehingga central tidak
# melihat apa pun yang aneh selain jeda singkat.
#
# Satu-satunya perubahan yang TIDAK bisa di-rollback dengan cara ini adalah
# migrasi skema outbox yang menaikkan versi. Lihat
# docs/operations/05-disaster-recovery.md.
set -euo pipefail

PREFIX="${FLEETVIEW_PREFIX:-/opt/fleetview}"
[[ $EUID -eq 0 ]] || { echo "jalankan sebagai root" >&2; exit 1; }

target="${1:-}"
if [[ -z "${target}" ]]; then
  current="$(basename "$(readlink -f "${PREFIX}/current")")"
  target="$(find "${PREFIX}/releases" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' \
    | sort | grep -v "^${current}$" | tail -1)"
  [[ -n "${target}" ]] || { echo "tidak ada rilis lain untuk dituju" >&2; exit 1; }
  echo "rilis aktif: ${current} -> menuju: ${target}"
fi

release="${PREFIX}/releases/${target}"
[[ -d "${release}" ]] || { echo "rilis tidak ada: ${release}" >&2; exit 1; }
"${release}/venv/bin/fleetview-edge" --version >/dev/null

systemctl stop fleetview-agent
ln -sfn "${release}" "${PREFIX}/current"
ln -sfn "${release}/venv" "${PREFIX}/venv"
ln -sfn "${release}/edge/deploy" "${PREFIX}/deploy"
systemctl start fleetview-agent
echo "kembali ke: ${target}"
