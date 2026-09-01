#!/usr/bin/env bash
# Pasang Edge Agent pada Raspberry Pi yang sudah di-provisioning.
#
# Skrip ini idempoten: menjalankannya ulang pada Pi yang sudah terpasang akan
# memutakhirkan berkas dan merestart layanan, tanpa menyentuh data.
set -euo pipefail

PREFIX="${FLEETVIEW_PREFIX:-/opt/fleetview}"
DATA_DIR="${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}"
CONFIG_DIR="${FLEETVIEW_CONFIG_DIR:-/etc/fleetview}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

[[ $EUID -eq 0 ]] || { echo "jalankan sebagai root" >&2; exit 1; }

id -u fleetview >/dev/null 2>&1 || useradd --system --home "${DATA_DIR}" --shell /usr/sbin/nologin fleetview
# dialout diperlukan untuk membaca port serial LP-A104.
usermod -aG dialout fleetview

install -d -o fleetview -g fleetview -m 0750 "${DATA_DIR}" "${DATA_DIR}/backups"
install -d -o root -g fleetview -m 0750 "${CONFIG_DIR}"

# Instalasi ke direktori bertanda versi, lalu symlink ditukar. Inilah yang
# membuat rollback menjadi satu perintah alih-alih instalasi ulang: rilis
# sebelumnya masih utuh di disk.
VERSION="$(cd "${SRC}" && git describe --tags --always 2>/dev/null || echo manual)"
RELEASE="${PREFIX}/releases/${VERSION}"
install -d "${RELEASE}"
cp -r "${SRC}/edge" "${SRC}/shared" "${SRC}/pyproject.toml" "${RELEASE}/"

# Runbook ikut dipasang. Unit systemd menunjuk ke sini lewat Documentation=, dan
# kapal tidak punya internet — panduan troubleshooting harus ada di perangkatnya
# sendiri, bukan di tempat yang hanya bisa dibuka dari darat.
if [[ -d "${SRC}/docs" ]]; then
  cp -r "${SRC}/docs" "${RELEASE}/"
  ln -sfn "${RELEASE}/docs" "${PREFIX}/docs"
fi

python3 -m venv "${RELEASE}/venv"
"${RELEASE}/venv/bin/pip" install --quiet --upgrade pip
"${RELEASE}/venv/bin/pip" install --quiet \
  "${RELEASE}/shared/common" "${RELEASE}/shared/contracts" \
  "${RELEASE}/edge/agent" "${RELEASE}/edge/console"

# Verifikasi sebelum menukar symlink. Rilis yang tidak bisa diimpor tidak boleh
# pernah menjadi rilis aktif.
"${RELEASE}/venv/bin/fleetview-edge" --version >/dev/null

ln -sfn "${RELEASE}" "${PREFIX}/current"
ln -sfn "${RELEASE}/venv" "${PREFIX}/venv"
ln -sfn "${RELEASE}/edge/deploy" "${PREFIX}/deploy"

# Satu perintah untuk teknisi yang naik ke kapal.
install -m 0755 "${SRC}/edge/deploy/scripts/fleetview-status.sh" /usr/local/bin/fleetview-status

install -m 0644 "${SRC}/edge/deploy/systemd/fleetview-agent.service" /etc/systemd/system/
install -m 0644 "${SRC}/edge/deploy/systemd/fleetview-agent-backup.service" /etc/systemd/system/
install -m 0644 "${SRC}/edge/deploy/systemd/fleetview-agent-backup.timer" /etc/systemd/system/
install -d /etc/systemd/journald.conf.d
install -m 0644 "${SRC}/edge/deploy/systemd/journald-fleetview.conf" \
  /etc/systemd/journald.conf.d/fleetview.conf

if [[ ! -f "${CONFIG_DIR}/agent.env" ]]; then
  echo "PERINGATAN: ${CONFIG_DIR}/agent.env belum ada." >&2
  echo "Kapal belum punya identitas. Lihat docs/operations/02-provisioning-raspberry-pi.md" >&2
fi

systemctl daemon-reload
systemctl restart systemd-journald
systemctl enable --now fleetview-agent-backup.timer

if [[ -f "${CONFIG_DIR}/agent.env" ]]; then
  systemctl enable --now fleetview-agent
  systemctl restart fleetview-agent
  echo "terpasang: ${VERSION}"
else
  systemctl enable fleetview-agent
  echo "terpasang: ${VERSION} (belum dijalankan; agent.env belum ada)"
fi
