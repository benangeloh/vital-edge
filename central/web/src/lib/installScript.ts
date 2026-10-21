/** Skrip pemasangan otomatis untuk satu kapal.
 *
 *  Dibuat di browser dari hasil pendaftaran, karena rahasia kapal hanya ada di
 *  layar itu: server hanya menyimpan hash-nya dan tidak bisa membuatnya lagi.
 *  Teknisi cukup menyalin satu berkas ke komputer kapal dan menjalankannya;
 *  tidak ada lagi menyalin dua potongan konfigurasi dengan tangan.
 *
 *  Skrip memakai jalur yang sudah ada: `install.sh` untuk memasang, lalu halaman
 *  setup Edge Console lokal untuk memasukkan kredensial. Dengan begitu kredensial
 *  diuji ke central sebelum apa pun ditulis, dan identitas kapal diambil dari
 *  central, bukan diketik. */

export interface InstallScriptInput {
  shipName: string;
  slug: string;
  /** Alamat central yang bisa dijangkau dari kapal. */
  centralUrl: string;
  clientId: string;
  secret: string;
  /** Alamat IP panel LP-A104. Kosong berarti belum ada panel (mode simulasi). */
  panelHost: string;
}

/** Repositori kode Edge. Bisa diganti saat skrip dijalankan lewat
 *  `FLEETVIEW_EDGE_REPO_URL`, atau dilewati sama sekali dengan
 *  `FLEETVIEW_EDGE_SRC` bila kodenya sudah disalin ke komputer kapal. */
export const DEFAULT_EDGE_REPO_URL = "https://github.com/AI-SPIL/vital-edge.git";
export const DEFAULT_EDGE_REF = "main";

/** Kutip nilai untuk shell: apa pun isinya dibaca sebagai teks biasa. */
function shq(value: string): string {
  return `'${value.replaceAll("'", `'\\''`)}'`;
}

/** Teks untuk komentar: satu baris, tanpa karakter kendali. */
function oneLine(value: string): string {
  return value.replace(/[\u0000-\u001f\u007f]+/g, " ").trim();
}

export function installScriptFileName(slug: string): string {
  const safe = slug.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "kapal";
  return `pasang-fleetview-${safe}.sh`;
}

/** Alamat `localhost` atau loopback hanya dikenal di komputer yang membuka
 *  dashboard, jadi tidak akan bisa dijangkau dari kapal. */
export function isReachableFromShip(url: string): boolean {
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (!/^https?:$/.test(new URL(url).protocol)) return false;
    return !(
      host === "localhost" ||
      host === "::1" ||
      host === "[::1]" ||
      host === "0.0.0.0" ||
      host.startsWith("127.")
    );
  } catch {
    return false;
  }
}

export function buildInstallScript(input: InstallScriptInput): string {
  const name = oneLine(input.shipName);
  const slug = oneLine(input.slug);

  return `#!/usr/bin/env bash
# Pemasangan otomatis FleetView Edge untuk ${name} (${slug}).
# Dibuat oleh dashboard FleetView.
#
# ISI BERKAS INI RAHASIA: memuat kunci kapal. Perlakukan seperti kata sandi, jangan
# dikirim lewat chat atau email. Berkas ini menghapus dirinya setelah berhasil.
#
# Untuk Ubuntu, Debian, atau Raspberry Pi OS. Jalankan di komputer kapal:
#   sudo bash ${installScriptFileName(slug)}
#
# Kapal tanpa internet: salin kode Edge ke komputer kapal (mis. lewat USB), lalu
#   sudo FLEETVIEW_EDGE_SRC=/jalur/ke/kode bash ${installScriptFileName(slug)}
set -euo pipefail

CENTRAL_URL=${shq(input.centralUrl)}
CLIENT_ID=${shq(input.clientId)}
SECRET=${shq(input.secret)}
PANEL_HOST=${shq(input.panelHost)}

REPO_URL="\${FLEETVIEW_EDGE_REPO_URL:-${DEFAULT_EDGE_REPO_URL}}"
REF="\${FLEETVIEW_EDGE_REF:-${DEFAULT_EDGE_REF}}"
PREFIX="\${FLEETVIEW_PREFIX:-/opt/fleetview}"
SRC="\${FLEETVIEW_EDGE_SRC:-\${PREFIX}/src}"
DATA_DIR="\${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}"
CONFIG_DIR="\${FLEETVIEW_CONFIG_DIR:-/etc/fleetview}"

gagal() { echo "GAGAL: $*" >&2; exit 1; }

[[ \$EUID -eq 0 ]] || gagal "jalankan dengan sudo: sudo bash \$0"
command -v apt-get >/dev/null 2>&1 || gagal "skrip ini hanya untuk Ubuntu, Debian, atau Raspberry Pi OS"

echo "==> [1/4] menyiapkan kode Edge"
if [[ -x "\${SRC}/edge/deploy/scripts/install.sh" ]]; then
  echo "    memakai kode yang sudah ada di \${SRC}"
else
  command -v git >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq git; }
  mkdir -p "\$(dirname "\${SRC}")"
  GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch "\${REF}" "\${REPO_URL}" "\${SRC}" \\
    || gagal "kode Edge tidak bisa diambil dari \${REPO_URL}. Periksa koneksi internet dan akses ke repositori, atau salin kodenya lalu jalankan dengan FLEETVIEW_EDGE_SRC=/jalur/ke/kode"
fi

echo "==> [2/4] memasang Edge Agent dan InfluxDB (beberapa menit)"
bash "\${SRC}/edge/deploy/scripts/install.sh"

echo "==> [3/4] mengatur sumber data"
if [[ -n "\${PANEL_HOST}" ]]; then
  EDGE_CONFIG="\${CONFIG_DIR}/edge.yaml" PANEL_HOST="\${PANEL_HOST}" \\
    "\${PREFIX}/venv/bin/python" - <<'PY_CONFIG'
import os
import yaml

path = os.environ["EDGE_CONFIG"]
with open(path, encoding="utf-8") as handle:
    config = yaml.safe_load(handle) or {}
collector = config.setdefault("collector", {})
collector["adapter"] = "lp_a104"
collector["lp_a104_host"] = os.environ["PANEL_HOST"]
collector.setdefault("lp_a104_port", 502)
with open(path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True)
PY_CONFIG
  echo "    membaca panel LP-A104 di \${PANEL_HOST}"
else
  echo "    PERINGATAN: alamat panel LP-A104 belum diisi." >&2
  echo "    Agent berjalan dalam mode simulasi, jadi data yang tampil di dashboard" >&2
  echo "    BUKAN data asli kapal. Isi alamat panel sebelum kapal dipakai." >&2
fi

echo "==> [4/4] menghubungkan ke pusat"
[[ -s "\${DATA_DIR}/setup.pin" ]] || gagal "PIN setup tidak ditemukan di \${DATA_DIR}/setup.pin. Perangkat ini mungkin sudah pernah dikonfigurasi."
CENTRAL_URL="\${CENTRAL_URL}" CLIENT_ID="\${CLIENT_ID}" SECRET="\${SECRET}" PIN="\$(cat "\${DATA_DIR}/setup.pin")" \\
  python3 - <<'PY_SETUP' || gagal "pendaftaran ke pusat ditolak (lihat pesan di atas). Perbaiki lalu jalankan ulang skrip ini."
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("CONSOLE_URL", "http://127.0.0.1:8080")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect)

for _ in range(45):
    try:
        opener.open(BASE + "/setup", timeout=5).close()
        break
    except urllib.error.HTTPError:
        break
    except OSError:
        time.sleep(2)
else:
    print("Edge Console tidak menjawab di " + BASE + ". Periksa: systemctl status fleetview-agent")
    sys.exit(2)

body = urllib.parse.urlencode(
    {
        "central_url": os.environ["CENTRAL_URL"],
        "client_id": os.environ["CLIENT_ID"],
        "secret": os.environ["SECRET"],
        "pin": os.environ["PIN"],
    }
).encode()

try:
    opener.open(urllib.request.Request(BASE + "/setup", data=body), timeout=60).close()
    print("Jawaban Edge Console tidak terduga.")
    sys.exit(1)
except urllib.error.HTTPError as err:
    location = err.headers.get("Location", "")
    if err.code in (302, 303, 307) and "selesai=1" in location:
        sys.exit(0)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    print(query.get("error", ["Edge Console menjawab HTTP " + str(err.code)])[0])
    sys.exit(1)
PY_SETUP

sleep 5
echo
echo "==================================================================="
echo "  Pemasangan selesai untuk ${name} (${slug})."
echo
echo "  Langkah berikutnya:"
echo "    1. Buka Edge Console: http://\$(hostname -I | awk '{print \$1}'):8080/sensors"
echo "       lalu tambahkan sensor yang terpasang di kapal."
echo "    2. Di dashboard pusat, kapal ini berstatus Online begitu data pertamanya sampai."
echo "    3. Periksa kapan saja dengan: sudo fleetview-status"
echo "==================================================================="

# Kunci kapal kini tersimpan di perangkat (\${CONFIG_DIR}/secrets.env). Salinan
# di berkas ini tidak diperlukan lagi.
rm -f -- "\${BASH_SOURCE[0]}"
exit 0
`;
}
