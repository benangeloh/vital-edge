#!/usr/bin/env bash
# Ambil HANYA bagian kapal dari monorepo, ke Raspberry Pi.
#
# Repo ini berisi dua sistem yang dideploy terpisah. Kapal hanya butuh:
#
#     shared/    format wire, logging, error  (dipakai KEDUA sisi)
#     edge/      agent + console
#     docs/      runbook operasional, untuk dibaca teknisi di kapal
#
# `central/` — API pusat dan dashboard React — tidak pernah dipasang maupun
# dijalankan di kapal. `install.sh` memang sudah mengabaikannya, jadi clone
# penuh pun aman; sparse checkout hanya membuat isinya tidak ikut mendarat di
# perangkat sama sekali. Bedanya bukan keamanan, melainkan kejelasan: teknisi
# yang membuka Pi tidak menemukan kode server yang tidak ada hubungannya, dan
# `node_modules` dashboard tidak pernah ikut tersalin.
#
# Kenapa tetap satu repo dan bukan dua: `shared/contracts` harus IDENTIK di
# kedua sisi. Kalau dipisah, format wire bisa menyimpang tanpa ada yang
# menangkapnya sampai sebuah kapal berhenti bisa menyetor data.
#
#   ./clone-edge.sh <url-repo> [tag-atau-branch] [tujuan]
set -euo pipefail

REPO="${1:-}"
REF="${2:-main}"
DEST="${3:-/opt/fleetview/src}"

[[ -n "${REPO}" ]] || { echo "pakai: $0 <url-repo> [ref] [tujuan]" >&2; exit 1; }

if [[ -d "${DEST}/.git" ]]; then
  echo "==> memperbarui ${DEST}"
  git -C "${DEST}" fetch --depth 1 origin "${REF}"
  git -C "${DEST}" checkout --detach FETCH_HEAD
else
  echo "==> mengambil bagian kapal dari ${REPO}"
  mkdir -p "${DEST}"
  # --filter=blob:none + sparse: hanya blob yang benar-benar dipakai yang
  # diunduh. Pada koneksi kapal, selisihnya nyata.
  git clone --filter=blob:none --no-checkout --depth 1 --branch "${REF}" "${REPO}" "${DEST}"
  git -C "${DEST}" sparse-checkout init --cone
  # docs ikut dengan sengaja. Kapal tidak punya internet, jadi panduan
  # troubleshooting justru paling dibutuhkan justru di tempat ia tidak bisa
  # diunduh. Ukurannya beberapa ratus KB teks — murah dibanding satu kunjungan
  # teknisi yang gagal karena tidak tahu harus memeriksa apa.
  git -C "${DEST}" sparse-checkout set shared edge docs
  git -C "${DEST}" checkout
fi

# Berkas di akar yang tetap dibutuhkan install.sh. Mode cone bekerja pada
# direktori; berkas akar ikut secara otomatis, jadi ini hanya pemeriksaan.
for f in pyproject.toml; do
  [[ -f "${DEST}/${f}" ]] || { echo "GAGAL: ${f} tidak ikut ter-checkout" >&2; exit 1; }
done

[[ -d "${DEST}/central" ]] && echo "  catatan: central/ ikut ter-checkout (bukan masalah, tidak dipasang)"

echo "==> siap: ${DEST}"
echo "    versi: $(git -C "${DEST}" describe --tags --always 2>/dev/null || echo '-')"
echo
echo "Lanjutkan dengan:  sudo ${DEST}/edge/deploy/scripts/install.sh"
