# Central Platform — Deployment Produksi via Docker

Status: menunggu review pengguna
Tanggal: 2026-09-14

## Konteks

Saat ini `central/` (api + web) di-deploy ke server produksi lewat systemd
langsung (`deploy/systemd/fleetview-central.service`) + nginx host
(`deploy/nginx/fleetview.conf`) + certbot, didokumentasikan penuh di
`docs/operations/03-central-deployment.md`. Postgres dan InfluxDB dipasang
manual lewat `apt-get`. Dev environment sudah punya `docker-compose.yml` di
root, tapi itu **hanya** untuk Postgres + InfluxDB saat development — belum
ada Dockerfile untuk `central/api` atau `central/web`.

Tujuan pekerjaan ini: central bisa **dijalankan penuh lewat Docker di
produksi** — satu perintah menyalakan seluruh stack (api, web, Postgres,
InfluxDB, reverse proxy + TLS) — menggantikan alur systemd+nginx+apt yang ada
sekarang.

## Non-goals

- **Edge tidak disentuh.** Edge (Raspberry Pi di kapal) tetap tanpa Docker —
  ini keputusan arsitektur yang sudah ada dan sengaja (satu proses, systemd,
  overhead minimal di perangkat terbatas). Lihat `edge/README.md`.
- `docker-compose.yml` (dev, root) **tidak diubah** — tetap untuk
  `make up`/`make dev` sehari-hari, hanya Postgres+InfluxDB, tanpa TLS/nginx.
  Compose produksi adalah file terpisah: `docker-compose.prod.yml`.
- Tidak menambah orkestrator (Kubernetes/Swarm). Target tetap satu server,
  sesuai spesifikasi hardware yang sudah didokumentasikan (8 inti, 32 GB RAM,
  4 TB NVMe) — `docker compose` saja cukup.
- Tidak mengubah kode aplikasi (`fleetview_central`) kecuali bila perlu
  menyesuaikan cara ia membaca config di dalam container (seharusnya tidak
  perlu — config sepenuhnya environment variable).

## Arsitektur

```
                    ┌─────────────────────────────────────────┐
                    │              docker-compose.prod.yml       │
                    │                                             │
  Internet ───443──▶│  proxy (nginx + static web build)          │
             ───80──▶│    - TLS terminasi                         │
                    │    - rate limit ingest/auth/query          │
                    │    - / → static SPA (fallback index.html)  │
                    │    - /api/* → api:8000                      │
                    │         │                                   │
                    │         ▼                                   │
                    │  api (fleetview-central-api, uvicorn)       │
                    │         │              │                    │
                    │         ▼              ▼                    │
                    │  postgres          influxdb                 │
                    │                                             │
                    │  migrate (job sekali jalan, jalan sebelum   │
                    │           api start)                        │
                    │  certbot (renewal periodik, webroot)        │
                    └─────────────────────────────────────────┘
                          │                    │
                    bind mount            bind mount
                    /var/lib/fleetview/   /var/lib/fleetview/
                    postgres-data         influx-data
```

Jaringan: satu docker network internal. Hanya `proxy` yang publish port ke
host (80, 443). `postgres` dan `influxdb` **tidak** publish port di
produksi (beda dari dev, yang publish 5432/8086 untuk akses lokal).

## Komponen

### 1. `central/api/Dockerfile`

Multi-stage:
- **builder**: base `python:3.12-slim`, install `uv`, `uv sync` /
  `pip install` paket `shared/common`, `shared/contracts`, `central/api` ke
  virtualenv.
- **runtime**: base `python:3.12-slim` juga (bukan distroless — perlu
  `alembic` CLI untuk job migrate pakai image yang sama), copy venv dari
  builder, copy source `central/api`. User non-root (`fleetview`, uid tetap
  supaya cocok dengan ownership bind mount). `ENTRYPOINT` tidak di-hardcode
  ke `fleetview-central` — command ditentukan per service di compose
  (`api` menjalankan `fleetview-central`, `migrate` menjalankan
  `alembic upgrade head`), supaya satu image dipakai dua service tanpa
  duplikasi build.
- Healthcheck: `curl -f http://127.0.0.1:8000/api/health` (endpoint yang
  sudah ada, tanpa dependency — cocok untuk healthcheck container).

### 2. `central/web/Dockerfile` (menjadi image untuk service `proxy`)

Multi-stage:
- **builder**: base `node:22-alpine`, `pnpm install --frozen-lockfile`,
  `pnpm --filter @fleetview/central-web build` → hasil `dist/`.
- **runtime**: base `nginx:1.27-alpine`, copy `dist/` ke
  `/usr/share/nginx/html`, copy config nginx hasil port dari
  `deploy/nginx/fleetview.conf` (lihat bagian 3).
- Healthcheck: `curl -f http://127.0.0.1/api/health` (lewat proxy, jadi
  sekaligus memverifikasi upstream `api` terjangkau dari sisi proxy).

### 3. Config nginx baru (`central/web/nginx/fleetview.conf`, dibawa ke image)

Port dari `deploy/nginx/fleetview.conf` yang sudah ada, dengan **satu
perbaikan**: `location /` sekarang menyajikan file statis SPA (dengan
`try_files $uri /index.html`) — bukan proxy ke API seperti config lama.
Hanya path di bawah `/api/` yang di-proxy ke upstream `api:8000`. Tiga zona
rate-limit (`ingest`, `auth`, `query`) dan exemption `/api/health` dari
rate-limit tetap dipertahankan persis, karena reasoning-nya (di komentar
config lama) masih berlaku sepenuhnya di container.

Ditambahkan: `location /.well-known/acme-challenge/` untuk validasi certbot
webroot, di-serve langsung dari volume yang di-share dengan container
`certbot`.

### 4. `docker-compose.prod.yml` (root)

Service (urutan startup lewat `depends_on` + `condition`):

1. `postgres` (postgres:16-alpine) — bind mount
   `/var/lib/fleetview/postgres-data:/var/lib/postgresql/data`. Healthcheck
   `pg_isready` (sama seperti compose dev).
2. `influxdb` (influxdb:2.7-alpine, versi sama dengan dev & edge — ini
   sudah jadi aturan tak tertulis di project ini) — bind mount
   `/var/lib/fleetview/influx-data:/var/lib/influxdb2`. Retensi bucket
   **365 hari** (beda dari dev yang 90 hari), sesuai
   `03-central-deployment.md` bagian 1.
3. `migrate` — image `central/api`, command `alembic upgrade head`,
   `depends_on: postgres: condition: service_healthy`. Tidak `restart` —
   sekali jalan lalu keluar.
4. `api` — image `central/api`, command `fleetview-central`,
   `depends_on: migrate: condition: service_completed_successfully`,
   `depends_on: influxdb: condition: service_healthy`. `env_file:`
   `/etc/fleetview/central.env` dan `/etc/fleetview/secrets.env` (format
   sama persis dengan yang didokumentasikan sekarang — **tidak berubah**,
   hanya cara ia disuntikkan yang berubah, dari `EnvironmentFile=` systemd
   ke `env_file:` compose). Tidak publish port ke host — hanya diakses
   `proxy` lewat docker network.
5. `proxy` — image `central/web`, `depends_on: api: condition:
   service_healthy`. Publish `80:80`, `443:443`. Volume: sertifikat TLS
   (`/etc/letsencrypt` dari host, read-only) dan volume shared
   acme-challenge dengan `certbot`.
6. `certbot` — image resmi `certbot/certbot`, tidak `restart: always`
   (dipicu manual/timer, bukan proses yang selalu hidup). Volume sama:
   `/etc/letsencrypt` dan acme-challenge webroot.

### 5. TLS & renewal

Setup awal (sekali, manual, sama seperti sekarang tapi lewat container):

```bash
docker compose -f docker-compose.prod.yml run --rm certbot certonly \
  --webroot -w /var/www/acme-challenge \
  -d fleetview.example.com --email <email> --agree-tos
```

Renewal: systemd timer baru di host (`fleetview-certbot-renew.timer`,
menggantikan timer bawaan certbot host yang tidak lagi relevan) menjalankan:

```bash
docker compose -f docker-compose.prod.yml run --rm certbot renew --quiet
docker compose -f docker-compose.prod.yml exec proxy nginx -s reload
```

`nginx -s reload` tanpa restart container — sertifikat baru terbaca tanpa
memutus koneksi yang sedang berjalan.

### 6. Data & permissions

Bind mount, bukan named volume — path eksplisit dan predictable, penting
untuk operasional 4 TB data:

```
/var/lib/fleetview/postgres-data/   (owner: uid postgres image, biasanya 999)
/var/lib/fleetview/influx-data/     (owner: uid influxdb image)
```

Dibuat lewat setup awal (`install-central.sh` baru — lihat bagian 9) dengan
ownership yang benar, karena bind mount **tidak** auto-chown seperti named
volume kadang melakukan di beberapa image.

### 7. Backup & diskwatch (adaptasi minimal)

`deploy/scripts/backup-central.sh` dan `deploy/scripts/diskwatch.sh` tetap
ada dan tetap dipicu systemd timer host (`fleetview-backup.timer`,
`fleetview-diskwatch.timer` — **tidak berubah mekanismenya**), hanya isinya
disesuaikan:

- `backup-central.sh`: `pg_dump` dan `influx backup` dijalankan lewat
  `docker compose -f docker-compose.prod.yml exec -T postgres pg_dump ...`
  dan `... exec -T influxdb influx backup ...`, output tetap ditulis ke
  `${FLEETVIEW_BACKUP_DIR}` di host (redirect stdout, bukan perlu volume
  tambahan).
- `diskwatch.sh`: variabel `PATHS` diarahkan ke
  `/var/lib/fleetview/postgres-data /var/lib/fleetview/influx-data
  /var/backups/fleetview` — path bind mount yang baru, bukan
  `/var/lib/postgresql` / `/var/lib/influxdb` versi apt-get lama.

### 8. Rollback & versioning

Image di-tag pakai `git describe --tags` (sama seperti variabel `$VERSION`
yang sudah dipakai sekarang), lewat build-arg atau tag eksplisit:

```bash
docker compose -f docker-compose.prod.yml build \
  --build-arg VERSION=$(git describe --tags --always)
docker compose -f docker-compose.prod.yml up -d
```

Rollback = checkout tag lama, `build` ulang (atau pull image lama dari
registry bila nanti ada CI yang push), `up -d`. Ini menggantikan skema
symlink `/opt/fleetview/releases/$VERSION` → `/opt/fleetview/current` yang
sekarang dipakai — image tag berperan sebagai penggantinya.

**Urutan upgrade tetap sama persis** dengan yang didokumentasikan sekarang
(prinsip ini tidak berubah, hanya perintahnya): **cadangkan → migrasi →
tukar rilis**. `migrate` job memastikan migrasi selalu jalan sebelum `api`
baru start — tapi backup tetap harus dijalankan manual dulu sebelum
`docker compose up -d` dengan image baru, karena migrate job tidak tahu
apakah backup sudah dilakukan.

### 9. Dokumen yang perlu ditulis ulang

- `docs/operations/03-central-deployment.md` — ditulis ulang total mengikuti
  alur Docker (server requirements tetap sama, tapi langkah 1–8 semua
  berubah ke container).
- `docs/operations/00-menjalankan.md` bagian **C** (di server central) —
  update perintah status/log/administrasi ke `docker compose` /
  `docker compose exec`.
- `central/README.md` — baris "nginx / Caddy menyajikan hasil build web/ —
  file statis, bukan proses Node" tetap benar secara prinsip, tapi
  tambahkan catatan bahwa di produksi ini berjalan sebagai container
  `proxy`.

### 10. Testing / verifikasi

Mengikuti section 7 dokumen lama (curl health check, TLS redirect, rate
limit 429) — tidak berubah endpoint yang dites, hanya konteksnya sekarang
containerized. Tambahan pemeriksaan khusus Docker:

```bash
docker compose -f docker-compose.prod.yml ps        # semua "healthy"
docker compose -f docker-compose.prod.yml logs api --tail 50
```

Diverifikasi juga: `migrate` keluar dengan exit code 0 dan `api` benar-benar
menunggu (`condition: service_completed_successfully`) — simulasikan
migrasi gagal (mis. skema konflik) dan pastikan `api` **tidak** start.

## Ringkasan berkas baru/diubah

| Berkas | Status |
|---|---|
| `central/api/Dockerfile` | baru |
| `central/web/Dockerfile` | baru |
| `central/web/nginx/fleetview.conf` | baru (port dari `deploy/nginx/fleetview.conf`) |
| `docker-compose.prod.yml` | baru |
| `deploy/scripts/install-central.sh` | baru — sekali jalan: buat direktori bind mount + ownership, salin `.env` contoh, setup awal certbot |
| `deploy/scripts/backup-central.sh` | diubah — `pg_dump`/`influx backup` lewat `docker compose exec` |
| `deploy/scripts/diskwatch.sh` | diubah — path bind mount baru |
| `deploy/systemd/fleetview-certbot-renew.timer` + `.service` | baru |
| `docker-compose.yml` (dev, root) | tidak berubah |
| `docs/operations/03-central-deployment.md` | ditulis ulang |
| `docs/operations/00-menjalankan.md` (bagian C) | diubah |
| `central/README.md` | diubah (catatan kecil) |

## Catatan build context (monorepo)

Kedua Dockerfile **tidak bisa** memakai `central/api/` atau `central/web/`
sebagai build context sendiri-sendiri — keduanya bergantung pada paket lokal
di luar direktorinya: `central/api` butuh `shared/common` dan
`shared/contracts` (lihat `pyproject.toml`), `central/web` butuh
`@fleetview/contracts` lewat `workspace:*` (pnpm workspace). Build context
untuk **kedua** Dockerfile harus **root repo**, dengan
`docker build -f central/api/Dockerfile .` / `-f central/web/Dockerfile .`,
dan `docker-compose.prod.yml` mengatur `build.context: .` +
`build.dockerfile: central/api/Dockerfile` per service.

## Risiko & catatan terbuka

- **Cutover dari instalasi produksi yang sudah berjalan** (bila central
  sudah live dengan Postgres/InfluxDB hasil `apt-get`): di luar cakupan
  spec ini. Bila saat implementasi ternyata sudah ada instalasi produksi
  nyata, perlu langkah migrasi data terpisah (`pg_dump` dari instalasi
  lama → restore ke container `postgres` baru, `influx backup`/`restore`
  serupa) sebelum cutover. Berdasarkan dokumen yang ada, proyek ini
  tampak masih pra-produksi (angka disk "perlu diverifikasi ulang setelah
  30 hari produksi" mengindikasikan belum ada histori produksi panjang),
  jadi diasumsikan **belum ada data produksi nyata untuk dimigrasi**. Ini
  perlu dikonfirmasi user sebelum eksekusi bila ternyata salah.
- Ukuran image builder Node (`pnpm install` penuh workspace monorepo) perlu
  dipastikan hanya menginstall dependency yang relevan untuk
  `@fleetview/central-web`, bukan seluruh workspace — akan dicek saat
  implementasi.
