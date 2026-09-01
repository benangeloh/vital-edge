#!/usr/bin/env bash
# Rollback central ke rilis sebelumnya.
#
# PENTING: rollback kode itu aman; rollback SKEMA tidak. Kalau rilis yang
# ditinggalkan menjalankan migrasi Alembic, rilis lama tidak akan mengenali
# skema barunya. Periksa dulu:
#     alembic current   dan   alembic history
# Lihat docs/operations/05-disaster-recovery.md bagian "Rollback melewati migrasi".
set -euo pipefail

PREFIX="${FLEETVIEW_PREFIX:-/opt/fleetview}"
[[ $EUID -eq 0 ]] || { echo "jalankan sebagai root" >&2; exit 1; }

target="${1:-}"
if [[ -z "${target}" ]]; then
  current="$(basename "$(readlink -f "${PREFIX}/current")")"
  target="$(find "${PREFIX}/releases" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' \
    | sort | grep -v "^${current}$" | tail -1)"
  [[ -n "${target}" ]] || { echo "tidak ada rilis lain untuk dituju" >&2; exit 1; }
fi

release="${PREFIX}/releases/${target}"
[[ -d "${release}" ]] || { echo "rilis tidak ada: ${release}" >&2; exit 1; }

echo "Revisi skema saat ini:"
"${PREFIX}/venv/bin/alembic" -c "${PREFIX}/current/central/api/alembic.ini" current || true
read -r -p "Rilis ${target} kompatibel dengan skema di atas? [y/N] " ok
[[ "${ok}" == "y" ]] || { echo "dibatalkan"; exit 1; }

systemctl stop fleetview-central
ln -sfn "${release}" "${PREFIX}/current"
ln -sfn "${release}/venv" "${PREFIX}/venv"
systemctl start fleetview-central
echo "kembali ke: ${target}"
