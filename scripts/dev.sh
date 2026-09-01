#!/usr/bin/env bash
# Menjalankan FleetView di komputer sendiri dengan satu perintah.
#
#   ./scripts/dev.sh            central + kapal simulasi + dashboard
#   ./scripts/dev.sh central    hanya central
#   ./scripts/dev.sh ship       hanya kapal simulasi (butuh central hidup)
#   ./scripts/dev.sh dashboard  hanya dashboard
#   ./scripts/dev.sh status     apa yang sedang jalan
#   ./scripts/dev.sh reset      hapus data kapal simulasi
#
# Ini BUKAN cara menjalankan di produksi. Di kapal dan di server pusat, systemd
# yang menjalankan — lihat docs/operations/00-menjalankan.md bagian B dan C.
set -uo pipefail

# Job control aktif supaya setiap layanan berjalan di process group-nya sendiri.
#
# Tanpa ini, `$!` menunjuk subshell pipa, bukan proses sesungguhnya di baliknya:
# `uv run` menjalankan Python sebagai anak, dan mematikan subshell meninggalkan
# Python tetap hidup memegang portnya. Gejalanya menyesatkan — Ctrl-C tampak
# berhasil, lalu menjalankan ulang gagal dengan "port sudah dipakai".
#
# Dengan job control, `$!` menjadi pemimpin process group, dan `kill -- -PID`
# menjangkau seluruh pohon proses sekaligus.
set -m

cd "$(dirname "${BASH_SOURCE[0]}")/.."

CENTRAL_PORT=8000
CONSOLE_PORT=8080
WEB_PORT=5173
DEVDATA=.devdata
PIDS=""

# Warna hanya bila keluarannya memang terminal. Di dalam pipe atau CI, kode ANSI
# hanya menjadi sampah yang menyulitkan pembacaan log.
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_MAGENTA=$'\033[35m'
else
  C_RESET=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_MAGENTA=""
fi

info()  { printf '%s\n' "${C_BLUE}==>${C_RESET} $*"; }
ok()    { printf '%s\n' "${C_GREEN}  ok${C_RESET} $*"; }
warn()  { printf '%s\n' "${C_YELLOW}  !${C_RESET} $*" >&2; }
die()   { printf '%s\n' "${C_RED}GAGAL${C_RESET} $*" >&2; exit 1; }

# ---------------------------------------------------------------- penghentian

CLEANED=""

cleanup() {
  trap '' INT TERM
  # Ctrl-C memicu trap INT, lalu keluarnya skrip memicu trap EXIT. Tanpa penjaga
  # ini pesan penghentian tercetak dua kali dan tampak seperti ada yang gagal.
  [ -z "${CLEANED}" ] || return 0
  CLEANED="ya"
  [ -n "${PIDS}" ] || return 0
  printf '\n%s\n' "${C_BLUE}==>${C_RESET} menghentikan..."
  # SIGTERM dulu, bukan SIGKILL: Edge Agent menutup outbox dengan rapi dan tidak
  # meninggalkan transaksi menggantung. Itu jalur yang sama dengan yang dipakai
  # systemd di kapal, jadi sekalian teruji di sini.
  # Job control dimatikan sebelum membunuh apa pun. Kalau tidak, bash mencetak
  # baris "[2]- Terminated: 15 ( "$@" 2>&1 | sed ..." untuk tiap job — potongan
  # kode internal yang tidak berarti apa-apa bagi pemakai dan membuat
  # penghentian yang normal tampak seperti kerusakan.
  set +m
  exec 2>/dev/null

  # Tanda minus = seluruh process group, bukan satu proses.
  for pid in ${PIDS}; do kill -TERM -- "-${pid}" 2>/dev/null; done
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    alive=""
    for pid in ${PIDS}; do kill -0 -- "-${pid}" 2>/dev/null && alive="yes"; done
    [ -z "${alive}" ] && break
    sleep 1
  done
  for pid in ${PIDS}; do kill -KILL -- "-${pid}" 2>/dev/null; done
  ok "berhenti"
}
trap cleanup INT TERM EXIT

# ------------------------------------------------------------------- prasyarat

port_busy() { lsof -ti:"$1" >/dev/null 2>&1; }

need() { command -v "$1" >/dev/null 2>&1 || die "$1 belum terpasang. $2"; }

check_prereqs() {
  need uv "Lihat https://docs.astral.sh/uv/"
  need docker "Butuh Docker untuk Postgres dan InfluxDB."
  docker info >/dev/null 2>&1 || die "Docker terpasang tetapi tidak berjalan. Nyalakan Docker Desktop."
}

# --------------------------------------------------------------------- setup

ensure_env() {
  if [ ! -f .env ]; then
    cp .env.example .env
    ok ".env dibuat dari .env.example"
  fi
}

ensure_stack() {
  info "menyalakan Postgres + InfluxDB"
  docker compose up -d >/dev/null 2>&1 || die "docker compose gagal"

  # Menunggu port terbuka tidak cukup: Postgres membuka port sebelum siap
  # menerima koneksi, dan migrasi yang dijalankan terlalu dini akan gagal dengan
  # pesan yang menyesatkan.
  printf '  menunggu database siap'
  for i in $(seq 1 60); do
    if docker compose exec -T postgres pg_isready -U fleetview >/dev/null 2>&1 \
       && curl -sf http://localhost:8086/health >/dev/null 2>&1; then
      printf '\r'; ok "database siap"
      return 0
    fi
    printf '.'; sleep 1
  done
  printf '\n'; die "database tidak siap setelah 60 detik. Coba: docker compose logs"
}

ensure_schema() {
  info "menerapkan migrasi"
  uv run alembic -c central/api/alembic.ini upgrade head >/dev/null 2>&1 \
    || die "migrasi gagal. Coba jalankan sendiri: make migrate"
  ok "skema mutakhir"
}

ensure_seed() {
  info "menyiapkan kapal simulasi dan operator"
  uv run python scripts/dev_seed.py || die "penyiapan data gagal"
}

# ------------------------------------------------------------------ menjalankan
#
# Tiap proses diberi awalan berwarna supaya tiga aliran log bisa dibaca
# bersamaan. Tanpa itu, keluaran ketiganya saling menimpa dan tidak terbaca.

run_prefixed() {
  local label="$1" color="$2"; shift 2
  ( "$@" 2>&1 | sed -u "s/^/${color}[${label}]${C_RESET} /" ) &
  PIDS="${PIDS} $!"
}

start_central() {
  port_busy "${CENTRAL_PORT}" && die "port ${CENTRAL_PORT} sudah dipakai. Hentikan proses itu dulu: lsof -ti:${CENTRAL_PORT} | xargs kill"
  run_prefixed central "${C_MAGENTA}" uv run fleetview-central
  printf '  menunggu central'
  for i in $(seq 1 40); do
    curl -sf "http://127.0.0.1:${CENTRAL_PORT}/api/health" >/dev/null 2>&1 && {
      printf '\r'; ok "central   http://localhost:${CENTRAL_PORT}/docs"; return 0; }
    printf '.'; sleep 1
  done
  printf '\n'; die "central tidak merespons"
}

start_ship() {
  port_busy "${CONSOLE_PORT}" && die "port ${CONSOLE_PORT} sudah dipakai."
  [ -f "${DEVDATA}/dev.env" ] || die "kredensial dev belum ada. Jalankan tanpa argumen dulu."
  # shellcheck disable=SC1090
  set -a; . "${DEVDATA}/dev.env"; set +a
  run_prefixed kapal "${C_GREEN}" uv run fleetview-edge --config edge/agent/config/edge.dev.yaml
  ok "console   http://localhost:${CONSOLE_PORT}"
}

start_dashboard() {
  command -v pnpm >/dev/null 2>&1 || { warn "pnpm belum terpasang; dashboard dilewati"; return 0; }
  [ -d central/web/node_modules ] || { warn "dependency frontend belum dipasang; jalankan 'make setup'"; return 0; }
  port_busy "${WEB_PORT}" && { warn "port ${WEB_PORT} sudah dipakai; dashboard dilewati"; return 0; }
  run_prefixed dasbor "${C_BLUE}" pnpm --filter @fleetview/central-web dev
  ok "dashboard http://localhost:${WEB_PORT}"
}

banner() {
  printf '\n%s\n' "${C_DIM}────────────────────────────────────────────────────────${C_RESET}"
  printf '%s\n' "  Dashboard  ${C_GREEN}http://localhost:${WEB_PORT}${C_RESET}   masuk: operator / bima2005"
  printf '%s\n' "  Console    ${C_GREEN}http://localhost:${CONSOLE_PORT}${C_RESET}"
  printf '%s\n' "  API docs   ${C_GREEN}http://localhost:${CENTRAL_PORT}/docs${C_RESET}"
  printf '%s\n' "${C_DIM}  Ctrl-C untuk berhenti${C_RESET}"
  printf '%s\n\n' "${C_DIM}────────────────────────────────────────────────────────${C_RESET}"
}

# ------------------------------------------------------------------ subperintah

cmd_status() {
  trap - EXIT
  for entry in "central ${CENTRAL_PORT}" "console ${CONSOLE_PORT}" "dashboard ${WEB_PORT}"; do
    set -- ${entry}
    if port_busy "$2"; then ok "$1 jalan di port $2"; else printf '  %s mati\n' "$1"; fi
  done
  docker compose ps 2>/dev/null | tail -n +2
}

cmd_reset() {
  trap - EXIT
  port_busy "${CONSOLE_PORT}" && die "hentikan kapal simulasi dulu"
  info "menghapus data kapal simulasi"
  rm -rf "${DEVDATA}"
  ok "outbox kapal dihapus"

  # Kedua sisi dibersihkan bersamaan, dan itu wajib. Menghapus outbox saja
  # membuat agent memulai sequence dari 1 lagi sementara central masih menyimpan
  # ledger untuk rentang itu — kiriman berikutnya ditolak `ingest.sequence_reused`,
  # yang benar sebagai perilaku tetapi membingungkan sebagai hasil dari reset.
  if docker compose ps postgres 2>/dev/null | grep -q Up; then
    uv run python scripts/dev_reset.py || warn "gagal membersihkan central"
  else
    warn "Postgres mati; ledger di central belum dibersihkan"
    warn "jalankan 'make up' lalu 'make dev-reset' lagi"
  fi
}

case "${1:-all}" in
  all)
    check_prereqs; ensure_env; ensure_stack; ensure_schema; ensure_seed
    start_central; start_ship; start_dashboard; banner; wait ;;
  central)
    check_prereqs; ensure_env; ensure_stack; ensure_schema; ensure_seed
    start_central; wait ;;
  ship)
    start_ship; wait ;;
  dashboard)
    start_dashboard; wait ;;
  status)  cmd_status ;;
  reset)   cmd_reset ;;
  *) die "subperintah tidak dikenal: $1 (lihat komentar di awal berkas)" ;;
esac
