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

# --- prasyarat --------------------------------------------------------------
#
# Dipasang di sini, bukan diasumsikan ada. Raspberry Pi OS Lite yang baru tidak
# membawa git, python3-venv, maupun sqlite3 — dan skrip yang gagal di tengah
# karena satu perintah tidak ada adalah pengalaman pertama yang buruk untuk
# teknisi yang baru memasang perangkat.
KURANG=()
command -v git      >/dev/null 2>&1 || KURANG+=(git)
command -v curl     >/dev/null 2>&1 || KURANG+=(curl)
command -v gpg      >/dev/null 2>&1 || KURANG+=(gnupg)
command -v sqlite3  >/dev/null 2>&1 || KURANG+=(sqlite3)
command -v openssl  >/dev/null 2>&1 || KURANG+=(openssl)
python3 -c "import venv" >/dev/null 2>&1 || KURANG+=(python3-venv)
if (( ${#KURANG[@]} )); then
  echo "==> memasang prasyarat: ${KURANG[*]}"
  apt-get update -qq
  apt-get install -y -qq "${KURANG[@]}"
fi

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
install -m 0755 "${SRC}/edge/deploy/scripts/detect-storage.sh" /usr/local/bin/fleetview-storage

install -m 0644 "${SRC}/edge/deploy/systemd/fleetview-agent.service" /etc/systemd/system/
install -m 0644 "${SRC}/edge/deploy/systemd/fleetview-agent-backup.service" /etc/systemd/system/
install -m 0644 "${SRC}/edge/deploy/systemd/fleetview-agent-backup.timer" /etc/systemd/system/
install -d /etc/systemd/journald.conf.d
install -m 0644 "${SRC}/edge/deploy/systemd/journald-fleetview.conf" \
  /etc/systemd/journald.conf.d/fleetview.conf

# --- InfluxDB lokal ---------------------------------------------------------
#
# Dipasang dan di-provisioning otomatis. Sebelumnya ini langkah manual dengan
# token yang harus disalin sendiri — sumber kesalahan yang baru ketahuan setelah
# agent berjalan dan setiap penulisan ditolak 401.
if ! command -v influx >/dev/null 2>&1; then
  echo "==> memasang InfluxDB"
  curl -fsSL https://repos.influxdata.com/influxdata-archive.key \
    | gpg --dearmor | tee /usr/share/keyrings/influxdata.gpg >/dev/null
  echo "deb [signed-by=/usr/share/keyrings/influxdata.gpg] \
https://repos.influxdata.com/debian stable main" \
    > /etc/apt/sources.list.d/influxdata.list
  apt-get update -qq && apt-get install -y -qq influxdb2
fi
systemctl enable --now influxdb >/dev/null 2>&1 || true

# Tunggu InfluxDB siap. Port terbuka tidak berarti siap menerima perintah, dan
# setup yang dijalankan terlalu dini gagal dengan pesan yang menyesatkan.
INFLUX_SIAP=""
for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8086/health >/dev/null 2>&1; then
    INFLUX_SIAP="ya"
    break
  fi
  sleep 1
done
if [[ -z "${INFLUX_SIAP}" ]]; then
  echo "PERINGATAN: InfluxDB tidak merespons setelah 60 detik." >&2
  echo "  Penulisan lokal akan ditolak. Sinkronisasi ke pusat TIDAK terpengaruh." >&2
  echo "  Periksa: systemctl status influxdb" >&2
fi

INFLUX_TOKEN_FILE="${CONFIG_DIR}/influx.token"
INFLUX_PASSWORD_FILE="${CONFIG_DIR}/influx-admin.password"
# `allowed: true` berarti InfluxDB belum pernah di-setup. Ini yang membedakan
# "sudah disiapkan sebelumnya" dari "tidak bisa dihubungi" — dua keadaan yang
# tindakannya berlawanan, dan menyamakannya membuat pemasangan yang rusak
# terlihat seperti pemasangan ulang yang wajar.
INFLUX_PERLU_SETUP=""
if [[ -n "${INFLUX_SIAP}" ]] \
   && curl -sf http://127.0.0.1:8086/api/v2/setup 2>/dev/null | grep -q '"allowed": *true'; then
  INFLUX_PERLU_SETUP="ya"
fi

if [[ -n "${INFLUX_SIAP}" && ! -s "${INFLUX_TOKEN_FILE}" ]]; then
  # Kata sandi admin tidak pernah dipakai lagi setelah ini; yang dipakai agent
  # hanya tokennya. Karena itu ia dibuat acak dan tidak disimpan di mana pun.
  # Kata sandi admin dibuat acak lalu DISIMPAN, tidak dibuang.
  #
  # Agent sendiri hanya memakai token, jadi secara teknis kata sandinya tidak
  # pernah dibutuhkan lagi. Tetapi teknisi kadang perlu membuka UI InfluxDB di
  # kapal untuk memastikan data benar-benar masuk, dan kata sandi yang hilang
  # membuat satu-satunya jalan adalah menyetel ulang lewat terminal — persis
  # yang ingin dihindari.
  INFLUX_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)"
  if influx setup --skip-verify --force \
       --username fleetview \
       --password "${INFLUX_PASSWORD}" \
       --org fleetview --bucket telemetry --retention 90d >/dev/null 2>&1; then
    echo "==> InfluxDB di-provisioning (retensi 90 hari)"
    # Kata sandi disimpan HANYA bila setup benar-benar berhasil.
    #
    # Pada InfluxDB yang sudah pernah disiapkan, `influx setup` gagal dan kata
    # sandi lamanya tetap berlaku. Menyimpan yang baru tanpa memeriksa akan
    # meninggalkan berkas yang isinya tidak pernah diterapkan — dan siapa pun
    # yang memakainya untuk login akan gagal tanpa tahu sebabnya.
    #
    # 0640 root:fleetview, bukan 0600: berbeda dari secrets.env yang dibaca
    # systemd sebagai root, berkas ini dibaca proses agent yang berjalan
    # sebagai fleetview.
    printf '%s\n' "${INFLUX_PASSWORD}" > "${INFLUX_PASSWORD_FILE}"
    chmod 0640 "${INFLUX_PASSWORD_FILE}"
    chown root:fleetview "${INFLUX_PASSWORD_FILE}" 2>/dev/null || true
  elif [[ -z "${INFLUX_PERLU_SETUP}" ]]; then
    echo "==> InfluxDB sudah disiapkan sebelumnya; kata sandi lama tetap berlaku"
  else
    echo "PERINGATAN: InfluxDB belum di-setup dan percobaan setup gagal." >&2
    echo "  Coba manual: influx setup --username fleetview --org fleetview \\" >&2
    echo "                 --bucket telemetry --retention 90d" >&2
  fi
  # Token diambil dari config CLI, lalu disimpan dengan izin ketat.
  if token="$(influx auth list --json 2>/dev/null | sed -n 's/.*"token": *"\([^"]*\)".*/\1/p' | head -1)"; then
    [[ -n "${token}" ]] && printf '%s\n' "${token}" > "${INFLUX_TOKEN_FILE}"
  fi
  chmod 0640 "${INFLUX_TOKEN_FILE}" 2>/dev/null || true
  chown root:fleetview "${INFLUX_TOKEN_FILE}" 2>/dev/null || true
fi

# agent.env selalu ditulis: isinya bukan rahasia, hanya penunjuk berkas config
# dan token InfluxDB lokal. Halaman setup di Console yang mengisi sisanya.
if [[ ! -f "${CONFIG_DIR}/agent.env" ]]; then
  {
    echo "FLEETVIEW_EDGE_CONFIG=${CONFIG_DIR}/edge.yaml"
    [[ -s "${INFLUX_TOKEN_FILE}" ]] && \
      echo "FLEETVIEW_STORAGE__INFLUX_TOKEN=$(cat "${INFLUX_TOKEN_FILE}")"
  } > "${CONFIG_DIR}/agent.env"
  chmod 0640 "${CONFIG_DIR}/agent.env"
  chown root:fleetview "${CONFIG_DIR}/agent.env"
fi

# Config awal: cukup untuk agent menyala dalam mode setup. Identitas kapal
# diisi lewat Edge Console, bukan dengan menyunting berkas ini.
if [[ ! -f "${CONFIG_DIR}/edge.yaml" ]]; then
  cp "${SRC}/edge/agent/config/sensors.example.yaml" "${CONFIG_DIR}/sensors.yaml"
  cat > "${CONFIG_DIR}/edge.yaml" <<YAML
# Ditulis oleh install.sh. Identitas kapal diisi lewat Edge Console.
environment: development
log_format: json

collector:
  adapter: simulator          # ganti ke lp_a104 setelah perangkat tersambung
  sensors_path: ${CONFIG_DIR}/sensors.yaml

storage:
  data_dir: /var/lib/fleetview
  outbox_path: /var/lib/fleetview/outbox.db
  influx_url: http://127.0.0.1:8086

console:
  enabled: true
  host: 0.0.0.0              # dijangkau laptop teknisi di jaringan kapal
  allow_lan: true            # keputusan sadar: halaman setup dilindungi PIN
  port: 8080
YAML
  chmod 0640 "${CONFIG_DIR}/edge.yaml"
  chown root:fleetview "${CONFIG_DIR}/edge.yaml"
fi

# Console menulis config saat setup, jadi direktorinya harus bisa ditulis
# pengguna fleetview — bukan hanya dibaca.
chown root:fleetview "${CONFIG_DIR}"
chmod 0770 "${CONFIG_DIR}"


systemctl daemon-reload
systemctl restart systemd-journald
systemctl enable --now fleetview-agent-backup.timer

systemctl enable --now fleetview-agent
systemctl restart fleetview-agent

# Alamat yang akan dibuka teknisi. Dicetak apa adanya supaya tidak perlu
# menebak IP perangkat sendiri.
ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
pin_file=/var/lib/fleetview/setup.pin
sleep 3

echo
echo "==================================================================="
echo "  FleetView Edge terpasang: ${VERSION}"
echo
if [[ -s "${pin_file}" ]]; then
  echo "  Buka di peramban:   http://${ip:-<alamat-pi>}:8080/setup"
  echo "  PIN setup      :    $(cat "${pin_file}")"
  echo
  echo "  Masukkan alamat pusat, ID klien, dan kunci rahasia di halaman itu."
  echo "  Nomor kapal dan ID perangkat diambil otomatis — tidak perlu diketik."
else
  echo "  Buka di peramban:   http://${ip:-<alamat-pi>}:8080/"
  echo "  Perangkat tampaknya sudah dikonfigurasi."
fi
echo
echo "  Penyimpanan lokal (InfluxDB)"
echo "    alamat  : http://127.0.0.1:8086     (dari laptop: http://${ip:-<pi>}:8086)"
echo "    org     : fleetview"
echo "    bucket  : telemetry, retensi 90 hari"
echo "    user    : fleetview"
# Yang menentukan adalah token yang BENAR-BENAR dipakai agent, yaitu isi
# agent.env — bukan keberadaan influx.token. Pada perangkat yang InfluxDB-nya
# disiapkan manual, berkas itu tidak ada padahal tokennya sudah terpasang, dan
# laporan "BELUM ADA" akan mengirim teknisi memburu masalah yang tidak ada.
if grep -q '^FLEETVIEW_STORAGE__INFLUX_TOKEN=.' "${CONFIG_DIR}/agent.env" 2>/dev/null; then
  echo "    token   : terpasang di ${CONFIG_DIR}/agent.env"
else
  echo "    token   : BELUM ADA — penulisan lokal akan ditolak"
fi
if [[ -s "${INFLUX_PASSWORD_FILE}" ]]; then
  echo "    sandi   : $(cat "${INFLUX_PASSWORD_FILE}")"
  echo "              (tersimpan di ${INFLUX_PASSWORD_FILE})"
else
  echo "    sandi   : tidak tersimpan — InfluxDB disiapkan manual"
  echo "              setel ulang: influx user password --name fleetview"
fi
echo
# Penyimpanan dilaporkan di akhir, bukan diabaikan. Perangkat yang datanya
# mendarat di kartu SD akan bekerja normal berbulan-bulan lalu mati total —
# dan itu satu-satunya kegagalan di sini yang tidak bisa diperbaiki dari darat.
/usr/local/bin/fleetview-storage 2>/dev/null | sed 's/^/  /' || true
echo "  Periksa kapan saja:  sudo fleetview-status"
echo "==================================================================="
