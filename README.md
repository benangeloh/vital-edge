# FleetView — Sistem Monitoring Kapal SPIL

Platform telemetry IoT offline-first untuk armada 70+ kapal.

Data selalu tersimpan di kapal terlebih dahulu. Internet bukan dependency —
LAN, Wi-Fi, seluler, dan USB semuanya hanya transport yang bisa saling menggantikan.

## Mulai cepat

**Device baru untuk development** (laptop/PC macOS atau Linux) — mensimulasikan
seluruh sistem (central + kapal + dashboard) di satu mesin:

1. Pasang [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/installation)
   (lewat `corepack enable`), dan [Docker](https://docs.docker.com/engine/install/)
   dengan Compose plugin — belum ada satupun di device ini, pasang ketiganya dulu.
2. Clone repo ini, lalu:

```bash
make setup     # sekali saja: dependency Python + Node
make dev       # jalankan semuanya
```

`make dev` menyalakan database, menerapkan migrasi, mendaftarkan kapal simulasi
beserta kredensialnya, lalu menjalankan central + Edge Agent + dashboard dengan
log berlabel di satu terminal. Ctrl-C menghentikan semuanya dengan rapi.

```
Dashboard  http://localhost:5173   masuk: operator / bima2005
Console    http://localhost:8080
API docs   http://localhost:8000/docs
```

Rinciannya, termasuk cara menjalankan sebagian saja:
[Cara Menjalankan](docs/operations/00-menjalankan.md).

**Device baru yang BUKAN untuk development** — Raspberry Pi yang akan naik ke
kapal sungguhan, atau server pusat produksi — jangan pakai `make dev` di atas.
Itu untuk simulasi di satu mesin, bukan instalasi produksi. Ikuti dokumen
instalasi yang sesuai:

| Device baru | Ikuti |
|---|---|
| Raspberry Pi di kapal | [Provisioning Raspberry Pi](docs/operations/02-provisioning-raspberry-pi.md), lalu [Onboarding kapal](docs/operations/07-ship-onboarding.md) |
| Server pusat | [Deployment central via Docker](docs/operations/03-central-deployment.md) — atau coba dulu produksi-mirip di device manapun lewat bagian di bawah |

Satu hal yang perlu diketahui sebelum instalasi di kapal sungguhan: **jalur
baca dari LP-A104 belum terkonfirmasi** — lihat
[status hardware](docs/hardware/LP-A104.md). Sampai itu beres, provisioning
Raspberry Pi tetap bisa dijalankan, tapi akuisisi sensor memakai adapter
simulator, bukan hardware sungguhan.

## Coba central via Docker (produksi-mirip)

Mencoba central berjalan persis seperti di server produksi (bukan `make dev`
yang pakai proses `uv run` langsung) — cocok untuk verifikasi sebelum deploy
sungguhan. Beda dari `make dev`: di sini Postgres, InfluxDB, api, DAN proxy
(nginx + dashboard + TLS) semuanya container, bukan hanya Postgres+InfluxDB.

Butuh [Docker](https://docs.docker.com/engine/install/) dengan Compose
plugin, dan `openssl` (sudah terpasang di macOS/Linux) untuk membuat
sertifikat TLS percobaan. Tidak butuh `uv`/`pnpm`/`sudo` — semua build
terjadi di dalam container, dan semua path yang di produksi mengarah ke
`/etc/fleetview` atau `/etc/letsencrypt` (butuh root) di sini dialihkan ke
folder temp lewat environment variable.

Urutan start otomatis (tidak perlu ditunggu manual — `depends_on` di
`docker-compose.prod.yml` yang mengatur): `postgres`+`influxdb` nyala dan
sehat lebih dulu → `migrate` menjalankan migrasi skema lalu keluar dengan
kode 0 → `api` baru start setelah `migrate` sukses → `proxy` (nginx, TLS,
reverse proxy) baru start setelah `api` sehat. Kalau `migrate` gagal, `api`
tidak akan pernah start — ini yang mencegah kode baru berjalan di atas skema
lama.

```bash
# 1. Siapkan konfigurasi lokal (nilai dummy, JANGAN dipakai di produksi)
mkdir -p /tmp/fleetview-central-try/certs/live/fleetview
cat > /tmp/fleetview-central-try/central.env <<'EOF'
FLEETVIEW_ENVIRONMENT=production
FLEETVIEW_LOG_FORMAT=json
FLEETVIEW_HOST=0.0.0.0
FLEETVIEW_PORT=8000
FLEETVIEW_INFLUX_URL=http://influxdb:8086
FLEETVIEW_INFLUX_ORG=fleetview
FLEETVIEW_INFLUX_BUCKET=telemetry
FLEETVIEW_CORS_ORIGINS=["https://localhost"]
FLEETVIEW_TRUST_PROXY_HEADERS=true
EOF
cat > /tmp/fleetview-central-try/secrets.env <<'EOF'
FLEETVIEW_POSTGRES_DSN=postgresql+asyncpg://fleetview:coba-saja-12345@postgres:5432/fleetview
FLEETVIEW_INFLUX_TOKEN=coba-saja-token-12345678901234567890
FLEETVIEW_JWT_SECRET=coba-saja-jwt-secret-minimal-32-karakter-xx
EOF
echo "coba-saja-12345" > /tmp/fleetview-central-try/postgres_password.txt
echo "coba-saja-12345" > /tmp/fleetview-central-try/influx_password.txt
echo "coba-saja-token-12345678901234567890" > /tmp/fleetview-central-try/influx_token.txt

# Sertifikat self-signed, hanya untuk percobaan lokal — browser/curl akan
# bilang "tidak tepercaya", itu wajar untuk sertifikat self-signed
openssl req -x509 -nodes -days 1 -newkey rsa:2048 \
  -keyout /tmp/fleetview-central-try/certs/live/fleetview/privkey.pem \
  -out /tmp/fleetview-central-try/certs/live/fleetview/fullchain.pem \
  -subj "/CN=localhost"

# Alihkan path yang di produksi butuh root ke folder temp di atas
export FLEETVIEW_CENTRAL_ENV=/tmp/fleetview-central-try/central.env
export FLEETVIEW_SECRETS_ENV=/tmp/fleetview-central-try/secrets.env
export FLEETVIEW_DATA_DIR=/tmp/fleetview-central-try/data
export FLEETVIEW_CERTS_DIR=/tmp/fleetview-central-try/certs
export FLEETVIEW_POSTGRES_PASSWORD_FILE=/tmp/fleetview-central-try/postgres_password.txt
export FLEETVIEW_INFLUX_PASSWORD_FILE=/tmp/fleetview-central-try/influx_password.txt
export FLEETVIEW_INFLUX_TOKEN_FILE=/tmp/fleetview-central-try/influx_token.txt

# 2. Build + jalankan seluruh stack
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d postgres influxdb migrate api proxy

# 3. Tunggu semua sehat, lalu cek
docker compose -f docker-compose.prod.yml ps
curl -sk https://localhost/api/health

# 4. Buat operator pertama untuk masuk dashboard (kata sandi diminta
#    interaktif, minimal 12 karakter — tidak pernah lewat argumen shell)
docker compose -f docker-compose.prod.yml exec api \
  fleetview-admin user add --username operator --roles admin

# 5. Buka dashboard (sertifikat self-signed, browser akan minta konfirmasi)
open https://localhost
```

Kalau langkah 3 berhasil (`curl` mengembalikan `"ok":true` dan
`docker compose ... ps` menunjukkan keempat service `healthy`), setup sudah
benar — tinggal buat operator (langkah 4) lalu buka dashboard (langkah 5).

Menghentikan dan membersihkan (jalankan di sesi shell yang sama, supaya
variabel `FLEETVIEW_*` di atas masih ter-`export` — kalau membuka terminal
baru, `export` ulang dulu agar tidak diam-diam mengarah ke `/etc/fleetview`
produksi):

```bash
docker compose -f docker-compose.prod.yml down -v
rm -rf /tmp/fleetview-central-try
```

`down -v` menghapus data test ini sepenuhnya — jangan jalankan terhadap
compose file yang menunjuk data produksi sungguhan.

Untuk deploy produksi sungguhan (domain nyata, TLS Let's Encrypt, backup
terjadwal): [docs/operations/03-central-deployment.md](docs/operations/03-central-deployment.md).

## Struktur

Ini **monorepo** berisi **dua sistem yang di-deploy terpisah**, plus kode yang
dipakai keduanya. Satu repo karena `shared/contracts` harus identik di kedua sisi —
kalau dipisah jadi dua repo, format wire bisa menyimpang tanpa ada yang menangkapnya.

```
shared/     Dipakai KEDUA sistem — format wire, logging, error, waktu
├── contracts/     fleetview-contracts   + @fleetview/contracts
└── common/        fleetview-common

edge/       SISTEM 1 → di-deploy ke setiap kapal (Raspberry Pi)
├── agent/         fleetview-edge-agent      collector, storage, sync
└── console/       fleetview-edge-console    UI operasional, jalan di proses agent

central/    SISTEM 2 → di-deploy ke pusat
├── api/           fleetview-central-api     modular monolith
└── web/           @fleetview/central-web    dashboard (file statis)

tools/      Simulator, CLI import USB (Phase 2 & 4)
docs/       Arsitektur dan catatan hardware
```

Tiap folder sistem punya README-nya sendiri:
[shared/](shared/README.md) · [edge/](edge/README.md) · [central/](central/README.md)

## Yang berjalan di mana

| Lokasi | Proses |
|---|---|
| Tiap kapal (×70) | `fleetview-edge.service` (satu proses: collector + sync + console) + `influxdb` |
| Pusat | `fleetview-central-api` + `postgres` + `influxdb` + nginx untuk file statis dashboard |

Bukan microservice. Dashboard bukan proses aplikasi — hasil `vite build`, disajikan
sebagai file statis.

## Menjalankan sebagian

```bash
make dev-central   # central saja
make dev-ship      # kapal simulasi saja (butuh central hidup)
make dev-status    # apa yang sedang jalan
make dev-reset     # hapus data kapal simulasi, di kedua sisi
```

Kalau menjalankan Edge Agent secara manual, pakai `edge.dev.yaml`, **bukan**
`edge.example.yaml`. Yang kedua adalah template produksi: adapter `lp_a104`
sengaja gagal keras karena jalur bacanya belum terkonfirmasi, dan
`environment: production` menolak start bila config belum lengkap.

## Perintah

| Perintah | Fungsi |
|---|---|
| `make check` | Semua pemeriksaan — inilah yang dijalankan CI |
| `make test` / `make test-cov` | Test, dengan atau tanpa coverage |
| `make fmt` / `make lint` | Format / periksa kode Python |
| `make typecheck` | mypy strict |
| `make schemas` | Regenerate JSON Schema dari model Pydantic |
| `make up` / `make down` | Stack dev Postgres + InfluxDB |

## Dokumentasi

Mulai dari [docs/README.md](docs/README.md).

| Kebutuhan | Dokumen |
|---|---|
| Menjalankan sistem | [Cara menjalankan](docs/operations/00-menjalankan.md) |
| **Memasang kapal baru (alur lengkap)** | **[Onboarding kapal](docs/operations/07-ship-onboarding.md)** |
| Memasang di Raspberry Pi | [Provisioning](docs/operations/02-provisioning-raspberry-pi.md) |
| Memasang server pusat | [Deployment central](docs/operations/03-central-deployment.md) |
| Ada yang tidak beres | [Troubleshooting](docs/operations/09-troubleshooting.md) |

Dua hal yang paling perlu diketahui sebelum menyentuh kode:

- [Protokol sync](docs/architecture/05-sync-protocol.md) — mengapa sebuah pembacaan
  tidak bisa hilang, dan mengapa `sequence` yang jadi patokan urutan, bukan jam.
- [Status LP-A104](docs/hardware/LP-A104.md) — **jalur baca dari perangkat lapangan
  belum terkonfirmasi.** Jangan menulis adapter hardware sungguhan sebelum ini beres.

## Konvensi

- Python 3.11 (sama dengan Raspberry Pi OS Bookworm), ruff + mypy strict
- Rahasia lewat environment variable, tidak pernah lewat berkas di repo
- Timestamp selalu membawa timezone; datetime naif ditolak di batas sistem
- Coverage minimal 80%
