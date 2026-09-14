# Central Docker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Central (`central/api` + `central/web`) bisa di-deploy penuh ke produksi lewat `docker compose -f docker-compose.prod.yml up -d` — satu perintah menyalakan api, web/proxy (nginx + TLS), Postgres, InfluxDB, migrasi skema, dan renewal sertifikat — menggantikan alur systemd+nginx+apt yang ada di `docs/operations/03-central-deployment.md`.

**Architecture:** Dua Dockerfile baru (`central/api/Dockerfile`, `central/web/Dockerfile`) di-build dengan context root repo (karena keduanya bergantung pada paket lokal `shared/`). `docker-compose.prod.yml` baru mengorkestrasi 6 service: `postgres`, `influxdb`, `migrate` (job sekali jalan), `api`, `proxy` (nginx: static SPA + reverse proxy + rate limit), `certbot` (renewal manual/timer). Data Postgres/InfluxDB di-bind-mount ke path eksplisit di host. Script backup/diskwatch yang sudah ada diadaptasi ke `docker compose exec`, dipicu systemd timer host yang sudah ada (mekanisme tidak berubah).

**Tech Stack:** Docker, Docker Compose v2, Python 3.11 (uv), nginx:1.27-alpine, node:22-alpine + pnpm 10.30.2, postgres:16-alpine, influxdb:2.7-alpine, certbot/certbot.

**Spec:** [docs/superpowers/specs/2026-09-14-central-docker-deployment-design.md](../specs/2026-09-14-central-docker-deployment-design.md)

## Global Constraints

- Python image: `python:3.11-slim` — harus sama dengan `.python-version` root dan Raspberry Pi OS Bookworm.
- pnpm version: `10.30.2` (pinned di root `package.json` `packageManager`).
- InfluxDB image: `influxdb:2.7-alpine` — versi yang sama dipakai di `docker-compose.yml` (dev) dan edge; jangan menyimpang.
- Postgres image: `postgres:16-alpine` — sama dengan `docker-compose.yml` (dev).
- Semua Dockerfile build context = **root repo**, bukan `central/api/` atau `central/web/` sendiri (dependency lokal `shared/common`, `shared/contracts`, `@fleetview/contracts`).
- `docker-compose.yml` (dev, root) **tidak boleh diubah**.
- Format `central.env` / `secrets.env` (nama variabel) **tidak boleh berubah** dari yang sudah didokumentasikan di `docs/operations/03-central-deployment.md` — hanya `FLEETVIEW_HOST` yang berubah nilainya dari `127.0.0.1` ke `0.0.0.0` (di Docker, `proxy` menjangkau `api` lewat network container, bukan loopback host).
- Rate-limit zona (`ingest`, `auth`, `query`) dan nilainya di nginx **harus identik** dengan `deploy/nginx/fleetview.conf` yang sudah ada — hanya `location /` yang berubah dari proxy ke static-serve.

---

### Task 1: Dockerfile untuk central/api + healthcheck endpoint verification

**Files:**
- Create: `central/api/Dockerfile`
- Create: `.dockerignore` (root)
- Test: manual build + run (tidak ada unit test Python baru — ini infra, diverifikasi dengan menjalankan container)

**Interfaces:**
- Consumes: `central/api/pyproject.toml` (deps: fleetview-contracts, fleetview-common, fastapi, uvicorn, dst — sudah ada), `shared/common/pyproject.toml`, `shared/contracts/pyproject.toml`, endpoint `GET /api/health` (sudah ada, tanpa dependency — dipakai `docs/operations/03-central-deployment.md` bagian 7).
- Produces: image `fleetview-central-api:local` dengan entrypoint venv di `/opt/venv`, command default `fleetview-central`, dan `alembic` CLI tersedia di PATH yang sama untuk dipakai Task 3 (job migrate). `WORKDIR /app` berisi `alembic.ini` dan `migrations/` di root `/app` (bukan di dalam package Python) — Task 3 menjalankan `alembic -c /app/alembic.ini upgrade head` dari sana.

- [ ] **Step 1: Buat `.dockerignore` di root**

```
.venv
.git
node_modules
central/web/node_modules
central/web/dist
.devdata
.pytest_cache
.ruff_cache
.mypy_cache
__pycache__
*.pyc
.coverage
coverage.xml
htmlcov
```

- [ ] **Step 2: Tulis `central/api/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv==0.11.*

WORKDIR /build
COPY shared/common shared/common
COPY shared/contracts shared/contracts
COPY central/api central/api

RUN uv venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

RUN uv pip install --no-cache \
    ./shared/common \
    ./shared/contracts \
    ./central/api

FROM python:3.11-slim AS runtime

RUN useradd --system --create-home --home-dir /home/fleetview \
    --shell /usr/sbin/nologin fleetview

COPY --from=builder /opt/venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY central/api/alembic.ini /app/alembic.ini
COPY central/api/migrations /app/migrations

USER fleetview

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

EXPOSE 8000

CMD ["fleetview-central"]
```

- [ ] **Step 3: Build image dan verifikasi**

Run: `docker build -f central/api/Dockerfile -t fleetview-central-api:local .`
Expected: build sukses, tahap terakhir `exporting to image` tanpa error.

- [ ] **Step 4: Verifikasi `alembic` dan `fleetview-central` ada di PATH**

Run: `docker run --rm --entrypoint python fleetview-central-api:local -c "import shutil; print(shutil.which('alembic')); print(shutil.which('fleetview-central'))"`
Expected: dua baris path di `/opt/venv/bin/`, bukan `None`.

- [ ] **Step 5: Verifikasi image tidak jalan sebagai root**

Run: `docker run --rm --entrypoint whoami fleetview-central-api:local`
Expected: output `fleetview`, bukan `root`.

- [ ] **Step 6: Commit**

```bash
git add central/api/Dockerfile .dockerignore
git commit -m "feat: tambah Dockerfile production untuk central/api"
```

---

### Task 2: Dockerfile untuk central/web + config nginx (proxy)

**Files:**
- Create: `central/web/Dockerfile`
- Create: `central/web/nginx/fleetview.conf`
- Create: `central/web/nginx/fleetview-proxy-headers.conf`

**Interfaces:**
- Consumes: `central/web/package.json` (build script `pnpm build`), `pnpm-workspace.yaml`, `pnpm-lock.yaml` (root), `shared/contracts/ts` (dependency workspace `@fleetview/contracts`), hasil build `central/web/dist/` (index.html + assets — sudah diverifikasi ada saat `pnpm build` dijalankan).
- Produces: image `fleetview-central-web:local`, listen port 80 di dalam container, upstream `api:8000` (nama service Docker Compose, ditentukan di Task 4) untuk semua path `/api/`, static file untuk selain itu dengan SPA fallback ke `/index.html`.

- [ ] **Step 1: Salin dan adaptasi config nginx dari `deploy/nginx/fleetview.conf`**

Buat `central/web/nginx/fleetview-proxy-headers.conf` — isinya **identik** dengan `deploy/nginx/fleetview-proxy-headers.conf` yang sudah ada:

```nginx
proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header Connection        "";
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Real-IP         $remote_addr;
```

- [ ] **Step 2: Tulis `central/web/nginx/fleetview.conf`**

Port dari `deploy/nginx/fleetview.conf`, dengan perubahan: upstream mengarah ke nama service `api` (bukan `127.0.0.1`), `location /` menyajikan static file (bukan proxy), dan tambahan location untuk acme-challenge certbot. TLS **tidak** diaktifkan di file ini — server block `443` dan path sertifikat dipisah ke override yang di-mount saat runtime (lihat Task 5), supaya image bisa dites tanpa sertifikat nyata dulu:

```nginx
upstream fleetview_central {
    server api:8000;
    keepalive 32;
}

limit_req_zone $binary_remote_addr zone=ingest:10m rate=60r/s;
limit_req_zone $binary_remote_addr zone=auth:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=query:10m rate=30r/s;

server {
    listen 80 default_server;
    server_name _;

    location /.well-known/acme-challenge/ {
        root /var/www/acme-challenge;
    }

    location = /api/health {
        access_log off;
        proxy_pass http://fleetview_central;
        include /etc/nginx/fleetview-proxy-headers.conf;
    }

    location /api/v1/ingest/ {
        limit_req zone=ingest burst=200 nodelay;
        limit_req_status 429;
        client_max_body_size 8m;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
        proxy_pass http://fleetview_central;
        include /etc/nginx/fleetview-proxy-headers.conf;
    }

    location /api/v1/auth/ {
        limit_req zone=auth burst=10 nodelay;
        limit_req_status 429;
        proxy_pass http://fleetview_central;
        include /etc/nginx/fleetview-proxy-headers.conf;
    }

    location /api/ {
        limit_req zone=query burst=60 nodelay;
        limit_req_status 429;
        proxy_pass http://fleetview_central;
        include /etc/nginx/fleetview-proxy-headers.conf;
    }

    location / {
        root /usr/share/nginx/html;
        try_files $uri /index.html;
    }
}
```

`docs/operations/03-central-deployment.md` yang lama mengandalkan HTTPS+redirect+TLS langsung di file ini (bagian TLS ditangani terpisah lewat override produksi — lihat Task 5, bukan dihapus).

- [ ] **Step 3: Tulis `central/web/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1
FROM node:22-alpine AS builder

RUN corepack enable && corepack prepare pnpm@10.30.2 --activate

WORKDIR /build
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY shared/contracts/ts shared/contracts/ts
COPY central/web central/web

RUN pnpm install --frozen-lockfile
RUN pnpm --filter @fleetview/central-web build

FROM nginx:1.27-alpine AS runtime

COPY --from=builder /build/central/web/dist /usr/share/nginx/html
COPY central/web/nginx/fleetview.conf /etc/nginx/conf.d/default.conf
COPY central/web/nginx/fleetview-proxy-headers.conf /etc/nginx/fleetview-proxy-headers.conf
RUN mkdir -p /var/www/acme-challenge

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["wget", "--quiet", "--tries=1", "--spider", "http://127.0.0.1/api/health"]

EXPOSE 80
```

- [ ] **Step 4: Build image dan verifikasi konten static ter-copy**

Run: `docker build -f central/web/Dockerfile -t fleetview-central-web:local .`
Expected: build sukses.

Run: `docker run --rm --entrypoint ls fleetview-central-web:local /usr/share/nginx/html`
Expected: muncul `index.html` dan `assets`.

- [ ] **Step 5: Verifikasi config nginx valid secara sintaks**

Run: `docker run --rm fleetview-central-web:local nginx -t`
Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful` — **catatan: ini akan gagal** karena upstream `api:8000` belum bisa di-resolve tanpa network compose. Jika gagal dengan pesan `host not found in upstream`, itu diverifikasi ulang nanti di Task 4 lewat `docker compose up` (bukan blocker di step ini — cukup pastikan tidak ada syntax error lain).

- [ ] **Step 6: Commit**

```bash
git add central/web/Dockerfile central/web/nginx/
git commit -m "feat: tambah Dockerfile production untuk central/web (nginx + static + proxy)"
```

---

### Task 3: docker-compose.prod.yml — postgres, influxdb, migrate

**Files:**
- Create: `docker-compose.prod.yml`
- Create: `.env.prod.example`

**Interfaces:**
- Consumes: image `fleetview-central-api:local` dari Task 1 (build lokal dulu, referensi registry ditambahkan nanti kalau ada CI).
- Produces: service `postgres` (healthcheck `pg_isready`), `influxdb` (healthcheck `influx ping`, retensi 365 hari), `migrate` (job sekali jalan, exit 0 = sukses) — dikonsumsi Task 4 lewat `depends_on: migrate: condition: service_completed_successfully`.

- [ ] **Step 1: Buat `.env.prod.example`**

```bash
# Salin ke /etc/fleetview/central.env (non-rahasia) di server produksi.
# Rahasia (postgres dsn, influx token, jwt secret) TERPISAH di
# /etc/fleetview/secrets.env — lihat docs/operations/03-central-deployment.md.

COMPOSE_PROJECT_NAME=fleetview-central

FLEETVIEW_DATA_DIR=/var/lib/fleetview

POSTGRES_USER=fleetview
POSTGRES_DB=fleetview

INFLUXDB_INIT_ORG=fleetview
INFLUXDB_INIT_BUCKET=telemetry
INFLUXDB_INIT_RETENTION=365d

FLEETVIEW_IMAGE_TAG=local
```

- [ ] **Step 2: Tulis `docker-compose.prod.yml` — bagian postgres, influxdb, migrate**

```yaml
name: fleetview-central

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-fleetview}
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
      POSTGRES_DB: ${POSTGRES_DB:-fleetview}
    volumes:
      - ${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}/postgres-data:/var/lib/postgresql/data
    secrets:
      - postgres_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-fleetview} -d ${POSTGRES_DB:-fleetview}"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  influxdb:
    image: influxdb:2.7-alpine
    environment:
      DOCKER_INFLUXDB_INIT_MODE: setup
      DOCKER_INFLUXDB_INIT_USERNAME: fleetview
      DOCKER_INFLUXDB_INIT_PASSWORD_FILE: /run/secrets/influx_password
      DOCKER_INFLUXDB_INIT_ORG: ${INFLUXDB_INIT_ORG:-fleetview}
      DOCKER_INFLUXDB_INIT_BUCKET: ${INFLUXDB_INIT_BUCKET:-telemetry}
      DOCKER_INFLUXDB_INIT_ADMIN_TOKEN_FILE: /run/secrets/influx_token
      DOCKER_INFLUXDB_INIT_RETENTION: ${INFLUXDB_INIT_RETENTION:-365d}
    volumes:
      - ${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}/influx-data:/var/lib/influxdb2
    secrets:
      - influx_password
      - influx_token
    healthcheck:
      test: ["CMD", "influx", "ping"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  migrate:
    build:
      context: .
      dockerfile: central/api/Dockerfile
    image: fleetview-central-api:${FLEETVIEW_IMAGE_TAG:-local}
    working_dir: /app
    entrypoint: []
    command: ["alembic", "-c", "/app/alembic.ini", "upgrade", "head"]
    env_file:
      - /etc/fleetview/central.env
      - /etc/fleetview/secrets.env
    depends_on:
      postgres:
        condition: service_healthy

secrets:
  postgres_password:
    file: /etc/fleetview/secrets/postgres_password.txt
  influx_password:
    file: /etc/fleetview/secrets/influx_password.txt
  influx_token:
    file: /etc/fleetview/secrets/influx_token.txt
```

Catatan desain: password Postgres/InfluxDB dipisah dari `secrets.env` yang isinya `FLEETVIEW_POSTGRES_DSN` (dipakai `api`, berisi password yang sama) — Docker secrets file dipakai khusus untuk kredensial **inisialisasi container resmi** (`POSTGRES_PASSWORD_FILE`, `DOCKER_INFLUXDB_INIT_PASSWORD_FILE`), sedangkan `api` tetap menerima `FLEETVIEW_POSTGRES_DSN`/`FLEETVIEW_INFLUX_TOKEN` lewat `secrets.env` seperti sebelumnya (format env tidak berubah, sesuai Global Constraints).

- [ ] **Step 3: Buat direktori secrets dummy untuk uji lokal**

```bash
mkdir -p /tmp/fleetview-secrets-test
echo "test-postgres-pw" > /tmp/fleetview-secrets-test/postgres_password.txt
echo "test-influx-pw" > /tmp/fleetview-secrets-test/influx_password.txt
echo "test-influx-token" > /tmp/fleetview-secrets-test/influx_token.txt
```

Untuk pengujian lokal langkah ini saja, override path secrets lewat env var atau symlink `/etc/fleetview -> /tmp/fleetview-secrets-test` sementara di mesin dev (bukan langkah produksi — produksi memakai path asli `/etc/fleetview/secrets/`).

- [ ] **Step 4: Jalankan postgres + influxdb, verifikasi healthy**

Run: `docker compose -f docker-compose.prod.yml up -d postgres influxdb`
Run: `docker compose -f docker-compose.prod.yml ps`
Expected: kedua service berstatus `healthy` dalam <30 detik.

- [ ] **Step 5: Jalankan migrate, verifikasi exit code 0**

Buat dulu `/etc/fleetview/central.env` dan `secrets.env` versi test lokal (isi minimal: `FLEETVIEW_POSTGRES_DSN` menunjuk ke `postgres` service, kredensial cocok dengan secrets Step 3).

Run: `docker compose -f docker-compose.prod.yml up migrate`
Expected: log menunjukkan alembic menjalankan revisi hingga `head`, container keluar dengan `exited with code 0`.

Run: `docker compose -f docker-compose.prod.yml ps -a migrate`
Expected: `Exit 0`.

- [ ] **Step 6: Bersihkan test lokal**

```bash
docker compose -f docker-compose.prod.yml down -v
rm -rf /tmp/fleetview-secrets-test
```

`-v` di sini aman karena ini data test lokal yang baru dibuat di Step 3–5, bukan data produksi.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.prod.yml .env.prod.example
git commit -m "feat: docker-compose.prod.yml — postgres, influxdb, migrate job"
```

---

### Task 4: docker-compose.prod.yml — api, proxy (tanpa TLS dulu)

**Files:**
- Modify: `docker-compose.prod.yml` (tambah service `api`, `proxy`)

**Interfaces:**
- Consumes: `migrate` (Task 3, `service_completed_successfully`), image `fleetview-central-web:local` (Task 2).
- Produces: service `api` dapat diakses service lain lewat DNS Docker Compose sebagai `api:8000`; `proxy` publish port 8080 ke host (sementara, bukan 80/443 — TLS ditambah Task 5) untuk verifikasi end-to-end lokal.

- [ ] **Step 1: Tambah service `api` ke `docker-compose.prod.yml`**

```yaml
  api:
    build:
      context: .
      dockerfile: central/api/Dockerfile
    image: fleetview-central-api:${FLEETVIEW_IMAGE_TAG:-local}
    env_file:
      - /etc/fleetview/central.env
      - /etc/fleetview/secrets.env
    depends_on:
      migrate:
        condition: service_completed_successfully
      influxdb:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 10s
    restart: unless-stopped
```

- [ ] **Step 2: Tambah service `proxy` (port sementara 8080 untuk uji lokal, bukan 80/443)**

```yaml
  proxy:
    build:
      context: .
      dockerfile: central/web/Dockerfile
    image: fleetview-central-web:${FLEETVIEW_IMAGE_TAG:-local}
    ports:
      - "8080:80"
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped
```

- [ ] **Step 3: Update `central.env` test lokal dengan `FLEETVIEW_HOST=0.0.0.0`**

Ini poin penting dari Global Constraints — `proxy` menjangkau `api` lewat network Docker, bukan loopback, jadi `api` harus listen di `0.0.0.0:8000`, bukan `127.0.0.1:8000` seperti di setup systemd lama.

- [ ] **Step 4: Jalankan seluruh stack (tanpa certbot dulu), verifikasi ujung ke ujung**

Run: `docker compose -f docker-compose.prod.yml up -d`
Run: `docker compose -f docker-compose.prod.yml ps`
Expected: semua service `healthy` kecuali `migrate` (`Exit 0`, sudah selesai).

Run: `curl -sf http://localhost:8080/api/health`
Expected: HTTP 200, body JSON status sehat.

Run: `curl -s http://localhost:8080/`
Expected: HTML dashboard (isi `index.html`), bukan JSON dari API — ini memverifikasi perbaikan `location /` di Task 2 (static, bukan proxy ke API).

- [ ] **Step 5: Verifikasi urutan startup: matikan migrate paksa, pastikan api tidak start**

Tes negatif untuk membuktikan `depends_on: condition: service_completed_successfully` benar-benar bekerja: ubah command `migrate` sementara jadi `["false"]` (exit 1), jalankan ulang stack dari bersih, verifikasi `api` **tidak pernah start**.

Run:
```bash
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml run --rm --entrypoint /bin/sh migrate -c "exit 1"
docker compose -f docker-compose.prod.yml up -d api
docker compose -f docker-compose.prod.yml ps api
```
Expected: `api` tidak dibuat / tidak jalan (compose menolak start karena dependency `migrate` belum `service_completed_successfully` yang sukses).

Kembalikan `migrate` ke command asli (`alembic upgrade head`) setelah tes ini.

- [ ] **Step 6: Bersihkan**

```bash
docker compose -f docker-compose.prod.yml down -v
```

- [ ] **Step 7: Commit**

```bash
git add docker-compose.prod.yml
git commit -m "feat: docker-compose.prod.yml — service api dan proxy"
```

---

### Task 5: TLS via certbot container (webroot) + port 80/443

**Files:**
- Modify: `docker-compose.prod.yml` (tambah service `certbot`, override port proxy ke 80/443, mount sertifikat)
- Create: `central/web/nginx/fleetview-tls.conf.template` (server block 443, di-mount terpisah supaya bisa diaktifkan setelah sertifikat ada)
- Create: `deploy/scripts/certbot-init.sh`
- Create: `deploy/scripts/certbot-renew.sh`
- Create: `deploy/systemd/fleetview-certbot-renew.service`
- Create: `deploy/systemd/fleetview-certbot-renew.timer`

**Interfaces:**
- Consumes: service `proxy` dari Task 4.
- Produces: `proxy` melayani HTTPS di 443 dengan sertifikat dari `/etc/letsencrypt` (bind mount host), redirect 80→443 untuk path selain acme-challenge.

- [ ] **Step 1: Ubah `central/web/nginx/fleetview.conf` — server block 80 jadi redirect, tambah server block 443**

Ganti isi `server { listen 80 ... }` di `central/web/nginx/fleetview.conf` (Task 2) supaya port 80 **hanya** melayani acme-challenge dan redirect selain itu ke HTTPS, lalu tambahkan server block 443 yang membawa seluruh location yang sudah ada sebelumnya (ingest/auth/query/health/static):

```nginx
server {
    listen 80 default_server;
    server_name _;

    location /.well-known/acme-challenge/ {
        root /var/www/acme-challenge;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    http2 on;
    server_name _;

    ssl_certificate     /etc/letsencrypt/live/fleetview/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/fleetview/privkey.pem;
    ssl_protocols TLSv1.3 TLSv1.2;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location = /api/health {
        access_log off;
        proxy_pass http://fleetview_central;
        include /etc/nginx/fleetview-proxy-headers.conf;
    }

    location /api/v1/ingest/ {
        limit_req zone=ingest burst=200 nodelay;
        limit_req_status 429;
        client_max_body_size 8m;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
        proxy_pass http://fleetview_central;
        include /etc/nginx/fleetview-proxy-headers.conf;
    }

    location /api/v1/auth/ {
        limit_req zone=auth burst=10 nodelay;
        limit_req_status 429;
        proxy_pass http://fleetview_central;
        include /etc/nginx/fleetview-proxy-headers.conf;
    }

    location /api/ {
        limit_req zone=query burst=60 nodelay;
        limit_req_status 429;
        proxy_pass http://fleetview_central;
        include /etc/nginx/fleetview-proxy-headers.conf;
    }

    location / {
        root /usr/share/nginx/html;
        try_files $uri /index.html;
    }
}
```

`server_name fleetview.example.com` sengaja diganti `_` (catch-all) di file yang di-commit ke repo — nama domain sungguhan disuntikkan lewat `sed` di `deploy/scripts/certbot-init.sh` (Step 4) saat instalasi, sama seperti operator dulu mengedit `server_name` manual di `03-central-deployment.md` lama.

- [ ] **Step 2: Update service `proxy` di `docker-compose.prod.yml`**

```yaml
  proxy:
    build:
      context: .
      dockerfile: central/web/Dockerfile
    image: fleetview-central-web:${FLEETVIEW_IMAGE_TAG:-local}
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /etc/letsencrypt:/etc/letsencrypt:ro
      - acme-challenge:/var/www/acme-challenge
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped

  certbot:
    image: certbot/certbot:latest
    volumes:
      - /etc/letsencrypt:/etc/letsencrypt
      - acme-challenge:/var/www/acme-challenge
    entrypoint: ["/bin/sh", "-c"]
    command: ["true"]

volumes:
  acme-challenge:
```

`certbot` sengaja tidak `restart: always` — ia dipicu manual (setup awal) atau lewat timer (renewal), bukan proses yang selalu hidup.

- [ ] **Step 3: Tulis `deploy/scripts/certbot-init.sh`**

```bash
#!/usr/bin/env bash
# Setup sertifikat TLS awal untuk Central Platform (sekali jalan, manual).
set -euo pipefail

DOMAIN="${1:?Pemakaian: certbot-init.sh <domain> <email>}"
EMAIL="${2:?Pemakaian: certbot-init.sh <domain> <email>}"
COMPOSE_FILE="${FLEETVIEW_COMPOSE_FILE:-/opt/fleetview/current/docker-compose.prod.yml}"

sed -i "s/server_name _;/server_name ${DOMAIN};/g" \
  /opt/fleetview/current/central/web/nginx/fleetview.conf

docker compose -f "${COMPOSE_FILE}" up -d proxy

docker compose -f "${COMPOSE_FILE}" run --rm certbot \
  certonly --webroot -w /var/www/acme-challenge \
  -d "${DOMAIN}" --email "${EMAIL}" --agree-tos --non-interactive \
  --cert-name fleetview

docker compose -f "${COMPOSE_FILE}" exec proxy nginx -s reload
```

- [ ] **Step 4: Tulis `deploy/scripts/certbot-renew.sh`**

```bash
#!/usr/bin/env bash
# Renewal TLS periodik — dipicu fleetview-certbot-renew.timer.
set -euo pipefail

COMPOSE_FILE="${FLEETVIEW_COMPOSE_FILE:-/opt/fleetview/current/docker-compose.prod.yml}"

docker compose -f "${COMPOSE_FILE}" run --rm certbot renew --quiet
docker compose -f "${COMPOSE_FILE}" exec proxy nginx -s reload
```

- [ ] **Step 5: Tulis unit systemd untuk renewal**

`deploy/systemd/fleetview-certbot-renew.service`:

```ini
[Unit]
Description=Renewal sertifikat TLS Central Platform (Docker)

[Service]
Type=oneshot
ExecStart=/opt/fleetview/deploy/scripts/certbot-renew.sh
```

`deploy/systemd/fleetview-certbot-renew.timer`:

```ini
[Unit]
Description=Jadwal renewal sertifikat TLS Central Platform

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Verifikasi lokal dengan self-signed cert (tanpa domain publik)**

Karena lingkungan dev tidak punya domain publik untuk Let's Encrypt sungguhan, verifikasi dengan sertifikat self-signed dulu:

```bash
mkdir -p /tmp/fleetview-tls-test/live/fleetview
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout /tmp/fleetview-tls-test/live/fleetview/privkey.pem \
  -out /tmp/fleetview-tls-test/live/fleetview/fullchain.pem \
  -days 1 -subj "/CN=localhost"
```

Mount `/tmp/fleetview-tls-test` sebagai `/etc/letsencrypt` (override volume sementara untuk tes), jalankan stack, lalu:

Run: `curl -sk https://localhost/api/health`
Expected: HTTP 200 (pakai `-k` karena sertifikat self-signed, bukan dari CA terpercaya — ini hanya memverifikasi TLS handshake dan routing, bukan validitas sertifikat).

Run: `curl -sI http://localhost | head -1`
Expected: `HTTP/1.1 301` (redirect ke HTTPS).

- [ ] **Step 7: Bersihkan test**

```bash
docker compose -f docker-compose.prod.yml down -v
rm -rf /tmp/fleetview-tls-test
chmod +x deploy/scripts/certbot-init.sh deploy/scripts/certbot-renew.sh
```

- [ ] **Step 8: Commit**

```bash
git add docker-compose.prod.yml central/web/nginx/fleetview.conf \
  deploy/scripts/certbot-init.sh deploy/scripts/certbot-renew.sh \
  deploy/systemd/fleetview-certbot-renew.service \
  deploy/systemd/fleetview-certbot-renew.timer
git commit -m "feat: TLS via certbot container (webroot) untuk central proxy"
```

---

### Task 6: Adaptasi backup-central.sh dan diskwatch.sh ke Docker

**Files:**
- Modify: `deploy/scripts/backup-central.sh`
- Modify: `deploy/scripts/diskwatch.sh`

**Interfaces:**
- Consumes: service `postgres`, `influxdb` dari `docker-compose.prod.yml` (Task 3).
- Produces: perilaku backup/diskwatch identik dengan sebelumnya (format output, exit code, log lewat `logger`), hanya sumber datanya lewat `docker compose exec`/path bind mount baru.

- [ ] **Step 1: Baca ulang `deploy/scripts/backup-central.sh` saat ini untuk memastikan tidak ada logic lain yang terlewat**

Sudah dibaca saat riset — isi lengkapnya: `pg_dump -Fc` lokal, verifikasi `pg_restore --list`, `influx backup` inkremental 48 jam, `sha256sum`, pemangkasan `mtime +KEEP_DAYS`. Semua logic ini **dipertahankan**, hanya baris yang memanggil `pg_dump`/`influx` langsung yang diubah jadi lewat `docker compose exec`.

- [ ] **Step 2: Ubah `backup-central.sh` — bagian PostgreSQL**

Ganti:
```bash
pg_dump -Fc -U "${PGUSER}" -d "${PGDATABASE}" -f "${dest}/postgres.dump"
```
Menjadi:
```bash
COMPOSE_FILE="${FLEETVIEW_COMPOSE_FILE:-/opt/fleetview/current/docker-compose.prod.yml}"
docker compose -f "${COMPOSE_FILE}" exec -T postgres \
  pg_dump -Fc -U "${PGUSER}" -d "${PGDATABASE}" > "${dest}/postgres.dump"
```

`pg_restore --list` di baris berikutnya **tidak berubah** — ia jalan di host terhadap file `.dump` yang sudah di-redirect ke host, jadi host tetap butuh `postgresql-client` terpasang untuk verifikasi ini (tidak berubah dari kebutuhan sebelumnya).

- [ ] **Step 3: Ubah `backup-central.sh` — bagian InfluxDB**

Ganti:
```bash
influx backup "${dest}/influx" \
  --bucket "${INFLUX_BUCKET}" \
  --start "..." \
  >/dev/null
```
Menjadi (backup ditulis ke path di dalam container dulu, lalu di-copy keluar — `influx backup` tidak bisa menulis langsung ke host lewat redirect seperti `pg_dump`):
```bash
docker compose -f "${COMPOSE_FILE}" exec -T influxdb \
  influx backup /tmp/influx-backup \
  --bucket "${INFLUX_BUCKET}" \
  --start "$(date -u -d '48 hours ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
            || date -u -v-48H +%Y-%m-%dT%H:%M:%SZ)" \
  >/dev/null
docker compose -f "${COMPOSE_FILE}" cp \
  influxdb:/tmp/influx-backup "${dest}/influx"
docker compose -f "${COMPOSE_FILE}" exec -T influxdb rm -rf /tmp/influx-backup
```

- [ ] **Step 4: Tambahkan guard `set -euo pipefail` tetap di awal (sudah ada, pastikan tidak terhapus)**

Verifikasi baris `set -euo pipefail` di baris 12 tetap ada setelah edit.

- [ ] **Step 5: Ubah `diskwatch.sh` — path default**

Ganti:
```bash
PATHS="${FLEETVIEW_DISK_PATHS:-/var/lib/influxdb /var/lib/postgresql /var/backups/fleetview}"
```
Menjadi:
```bash
PATHS="${FLEETVIEW_DISK_PATHS:-/var/lib/fleetview/postgres-data /var/lib/fleetview/influx-data /var/backups/fleetview}"
```

- [ ] **Step 6: Uji `backup-central.sh` terhadap stack Task 3–4 yang jalan lokal**

Jalankan stack Task 4 (postgres, influxdb, migrate, api sehat), lalu:

Run: `FLEETVIEW_COMPOSE_FILE=docker-compose.prod.yml FLEETVIEW_BACKUP_DIR=/tmp/fleetview-backup-test ./deploy/scripts/backup-central.sh`
Expected: output `cadangan selesai: ...`, dan `/tmp/fleetview-backup-test/<timestamp>/postgres.dump` + `SHA256SUMS` ada dan tidak kosong.

- [ ] **Step 7: Uji `diskwatch.sh` terhadap path baru**

Run: `FLEETVIEW_DISK_PATHS="/tmp" ./deploy/scripts/diskwatch.sh; echo "exit=$?"`
Expected: `exit=0` (asumsi `/tmp` di mesin dev tidak penuh) — ini memverifikasi script tetap berjalan tanpa error sintaks setelah perubahan path default.

- [ ] **Step 8: Bersihkan**

```bash
rm -rf /tmp/fleetview-backup-test
docker compose -f docker-compose.prod.yml down -v
```

- [ ] **Step 9: Commit**

```bash
git add deploy/scripts/backup-central.sh deploy/scripts/diskwatch.sh
git commit -m "fix: adaptasi backup-central.sh dan diskwatch.sh ke stack Docker"
```

---

### Task 7: Script install-central.sh (setup awal server)

**Files:**
- Create: `deploy/scripts/install-central.sh`

**Interfaces:**
- Consumes: `docker-compose.prod.yml`, `.env.prod.example`.
- Produces: direktori bind mount dengan ownership benar, `.env` awal, sertifikat awal (memanggil `certbot-init.sh` dari Task 5).

- [ ] **Step 1: Tulis `deploy/scripts/install-central.sh`**

```bash
#!/usr/bin/env bash
# Setup awal server produksi Central Platform — sekali jalan.
# Prasyarat: Docker + Docker Compose plugin sudah terpasang di server.
set -euo pipefail

DOMAIN="${1:?Pemakaian: install-central.sh <domain> <email>}"
EMAIL="${2:?Pemakaian: install-central.sh <domain> <email>}"
DATA_DIR="${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}"

sudo mkdir -p "${DATA_DIR}/postgres-data" "${DATA_DIR}/influx-data"
# uid 70 = postgres di image postgres:16-alpine, uid 1000 = influxdb di
# image influxdb:2.7-alpine (bukan root, image resmi berjalan non-root).
sudo chown -R 70:70 "${DATA_DIR}/postgres-data"
sudo chown -R 1000:1000 "${DATA_DIR}/influx-data"

sudo install -d -m 0750 /etc/fleetview/secrets
[[ -f /etc/fleetview/central.env ]] || \
  sudo cp .env.prod.example /etc/fleetview/central.env

if [[ ! -f /etc/fleetview/secrets/postgres_password.txt ]]; then
  openssl rand -hex 16 | sudo tee /etc/fleetview/secrets/postgres_password.txt >/dev/null
fi
if [[ ! -f /etc/fleetview/secrets/influx_password.txt ]]; then
  openssl rand -hex 16 | sudo tee /etc/fleetview/secrets/influx_password.txt >/dev/null
fi
if [[ ! -f /etc/fleetview/secrets/influx_token.txt ]]; then
  openssl rand -hex 32 | sudo tee /etc/fleetview/secrets/influx_token.txt >/dev/null
fi
sudo chmod 0600 /etc/fleetview/secrets/*.txt

echo "Berikutnya (manual, sekali):"
echo "1. Lengkapi /etc/fleetview/secrets.env (FLEETVIEW_POSTGRES_DSN, FLEETVIEW_INFLUX_TOKEN, FLEETVIEW_JWT_SECRET)"
echo "   — nilai token/password harus SAMA dengan isi /etc/fleetview/secrets/*.txt di atas."
echo "2. docker compose -f docker-compose.prod.yml up -d postgres influxdb migrate api"
echo "3. ./deploy/scripts/certbot-init.sh ${DOMAIN} ${EMAIL}"
echo "4. sudo cp deploy/systemd/fleetview-certbot-renew.* /etc/systemd/system/"
echo "   sudo systemctl enable --now fleetview-certbot-renew.timer"
```

Catatan uid: verifikasi uid image resmi berbeda dari asumsi di atas sebelum dipakai di produksi — Step 2 di bawah memverifikasi ini secara langsung, bukan asumsi buta.

- [ ] **Step 2: Verifikasi uid image resmi yang diasumsikan di script**

Run: `docker run --rm postgres:16-alpine id postgres`
Run: `docker run --rm influxdb:2.7-alpine id influxdb`
Expected: catat uid sungguhan, sesuaikan angka `chown` di Step 1 bila berbeda dari asumsi (70 dan 1000).

- [ ] **Step 3: `chmod +x` dan commit**

```bash
chmod +x deploy/scripts/install-central.sh
git add deploy/scripts/install-central.sh
git commit -m "feat: script install-central.sh untuk setup awal server Docker"
```

---

### Task 8: Tulis ulang docs/operations/03-central-deployment.md

**Files:**
- Modify: `docs/operations/03-central-deployment.md`

**Interfaces:**
- Consumes: seluruh Task 1–7.
- Produces: dokumen operasional yang bisa diikuti operator dari nol tanpa membaca kode.

- [ ] **Step 1: Ganti bagian "1. Basis data" dan "2. Pasang aplikasi"**

Hapus langkah `apt-get install postgresql-15 nginx`, `git clone` + `venv` manual. Ganti dengan:

```markdown
## 1–2. Pasang Docker dan aplikasi

Prasyarat: Docker Engine + Compose plugin ([docs resmi](https://docs.docker.com/engine/install/)).

    sudo mkdir -p /opt/fleetview/src
    sudo git clone <url-repo> /opt/fleetview/src
    cd /opt/fleetview/src && sudo git checkout <tag rilis>
    sudo ./deploy/scripts/install-central.sh fleetview.example.com admin@example.com
```
```

- [ ] **Step 2: Ganti bagian "3. Konfigurasi"**

Pertahankan **isi** `central.env`/`secrets.env` seperti sebelumnya (nama variabel tidak berubah — Global Constraints), hanya ubah `FLEETVIEW_HOST=127.0.0.1` menjadi `FLEETVIEW_HOST=0.0.0.0` dan tambahkan catatan mengapa (proxy menjangkau api lewat network Docker, bukan loopback host — beda dari setup systemd lama di mana nginx dan aplikasi berbagi network stack yang sama).

- [ ] **Step 3: Ganti bagian "4. Migrasi skema"**

```markdown
## 4. Migrasi skema

Otomatis: service `migrate` di `docker-compose.prod.yml` jalan sekali setiap
`docker compose up`, dan `api` menunggu sampai migrasi sukses sebelum start.

    docker compose -f docker-compose.prod.yml logs migrate
    docker compose -f docker-compose.prod.yml ps migrate   # harus "Exit 0"
```

- [ ] **Step 4: Ganti bagian "5. nginx dan TLS"**

Rujuk ke `deploy/scripts/certbot-init.sh` (Task 5) untuk setup awal dan `fleetview-certbot-renew.timer` untuk renewal — pertahankan penjelasan **kenapa** 429 bukan 503 dan tiga zona rate-limit (reasoning ini tidak berubah, hanya cara deploy-nya).

- [ ] **Step 5: Ganti bagian "6. Nyalakan"**

```markdown
## 6. Nyalakan

    docker compose -f docker-compose.prod.yml up -d
    docker compose -f docker-compose.prod.yml ps    # semua "healthy"
```

- [ ] **Step 6: Ganti bagian "6b. Operator pertama"**

```markdown
## 6b. Operator pertama

    docker compose -f docker-compose.prod.yml exec api \
      fleetview-admin user add --username <nama> --roles admin
```

- [ ] **Step 7: Pertahankan bagian "7. Verifikasi" apa adanya (curl health check, TLS, rate limit) — endpoint dan perilaku tidak berubah**

- [ ] **Step 8: Ganti bagian "8. Pemutakhiran"**

```markdown
## 8. Pemutakhiran

    sudo systemctl start fleetview-backup.service   # cadangkan dulu, selalu
    cd /opt/fleetview/src && sudo git fetch && sudo git checkout <tag baru>
    docker compose -f docker-compose.prod.yml build \
      --build-arg VERSION=$(git describe --tags --always)
    docker compose -f docker-compose.prod.yml up -d
```

Urutan **cadangkan → migrasi (otomatis lewat `migrate` job) → tukar rilis**
tetap sama persis prinsipnya dengan sebelumnya.

- [ ] **Step 9: Commit**

```bash
git add docs/operations/03-central-deployment.md
git commit -m "docs: tulis ulang 03-central-deployment.md untuk alur Docker"
```

---

### Task 9: Update docs/operations/00-menjalankan.md bagian C dan central/README.md

**Files:**
- Modify: `docs/operations/00-menjalankan.md` (bagian "C — Di server central")
- Modify: `central/README.md`

**Interfaces:**
- Consumes: Task 8.

- [ ] **Step 1: Ganti perintah di bagian C1–C7 `00-menjalankan.md`**

Ganti tiap `sudo systemctl status/restart fleetview-central` menjadi
`docker compose -f docker-compose.prod.yml ps/restart api`, `journalctl -u
fleetview-central` menjadi `docker compose -f docker-compose.prod.yml logs
api`, dan `fleetview-admin`/`alembic` yang dipanggil lewat venv menjadi
`docker compose -f docker-compose.prod.yml exec api <perintah>`. Pertahankan
seluruh penjelasan **mengapa** (dua endpoint health berbeda, retensi Influx,
urutan backup-migrasi-restart) — hanya perintahnya yang berubah.

- [ ] **Step 2: Update `central/README.md` baris "Yang berjalan di pusat"**

Ganti:
```
fleetview-central-api    satu proses, stateless, bisa diskalakan jadi N replika
postgres                 metadata relational
influxdb                 telemetry time-series
nginx / Caddy            menyajikan hasil build web/ — file statis, bukan proses Node
```
Menjadi:
```
docker compose -f docker-compose.prod.yml    orkestrasi seluruh stack produksi
├── api        fleetview-central-api, stateless, bisa diskalakan jadi N replika
├── proxy      nginx: TLS, rate-limit, file statis web/, reverse proxy ke api
├── postgres   metadata relational
├── influxdb   telemetry time-series
└── migrate    job alembic, jalan sekali tiap deploy sebelum api start
```

- [ ] **Step 3: Commit**

```bash
git add docs/operations/00-menjalankan.md central/README.md
git commit -m "docs: update 00-menjalankan.md dan central/README.md untuk alur Docker"
```

---

### Task 10: Update README.md root dengan langkah coba-jalankan cepat

**Files:**
- Modify: `README.md` (root)

**Interfaces:**
- Consumes: Task 1–9 (image harus bisa dibuild, stack harus bisa `up -d`).

Task ini yang menjawab permintaan eksplisit: "update README.md nya juga
langkah-langkahnya agar saya coba" — bagian baru ini harus bisa diikuti
langsung tanpa baca dokumen lain, memakai kredensial dummy lokal (bukan
produksi sungguhan).

- [ ] **Step 1: Tambah bagian baru setelah "## Mulai cepat" — "## Coba central via Docker (produksi-mirip)"**

```markdown
## Coba central via Docker (produksi-mirip)

Mencoba central berjalan persis seperti di server produksi (bukan `make dev`
yang pakai proses `uv run` langsung) — cocok untuk verifikasi sebelum deploy
sungguhan. Beda dari `make dev`: di sini Postgres, InfluxDB, api, DAN web
semuanya container, bukan hanya Postgres+InfluxDB.

Butuh [Docker](https://docs.docker.com/engine/install/) dengan Compose
plugin. Tidak butuh `uv`/`pnpm` terpasang di mesin — semua build terjadi di
dalam container.

```bash
# 1. Siapkan konfigurasi lokal (nilai dummy, JANGAN dipakai di produksi)
mkdir -p /tmp/fleetview-central-try
cat > /tmp/fleetview-central-try/central.env <<'EOF'
FLEETVIEW_ENVIRONMENT=production
FLEETVIEW_LOG_FORMAT=json
FLEETVIEW_HOST=0.0.0.0
FLEETVIEW_PORT=8000
FLEETVIEW_INFLUX_URL=http://influxdb:8086
FLEETVIEW_INFLUX_ORG=fleetview
FLEETVIEW_INFLUX_BUCKET=telemetry
FLEETVIEW_CORS_ORIGINS=["http://localhost:8080"]
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

# 2. Build + jalankan seluruh stack
export FLEETVIEW_CENTRAL_ENV=/tmp/fleetview-central-try/central.env
export FLEETVIEW_SECRETS_ENV=/tmp/fleetview-central-try/secrets.env
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d postgres influxdb migrate api proxy

# 3. Tunggu semua sehat, lalu cek
docker compose -f docker-compose.prod.yml ps
curl -sf http://localhost:8080/api/health

# 4. Buat operator pertama untuk masuk dashboard
docker compose -f docker-compose.prod.yml exec api \
  fleetview-admin user add --username operator --roles admin

# 5. Buka dashboard
open http://localhost:8080
```

Menghentikan dan membersihkan:

```bash
docker compose -f docker-compose.prod.yml down -v
rm -rf /tmp/fleetview-central-try
```

`down -v` menghapus data test ini sepenuhnya — jangan jalankan terhadap
compose file yang menunjuk data produksi sungguhan.

Untuk deploy produksi sungguhan (domain nyata, TLS Let's Encrypt, backup
terjadwal): [docs/operations/03-central-deployment.md](docs/operations/03-central-deployment.md).
```

- [ ] **Step 2: Jalankan seluruh urutan di Step 1 secara nyata untuk verifikasi README akurat**

Ini bukan sekadar menyalin — jalankan persis seperti yang tertulis di README dari direktori bersih, catat bila ada perintah yang gagal atau butuh penyesuaian (mis. field `env_file` di `docker-compose.prod.yml` saat ini menunjuk path tetap `/etc/fleetview/*.env` — Step ini butuh `docker-compose.prod.yml` menerima path lewat variabel, lihat Step 3).

- [ ] **Step 3: Sesuaikan `docker-compose.prod.yml` supaya path env-file bisa dioverride (dibutuhkan agar Step 1 di atas benar-benar bisa dijalankan tanpa akses `sudo` ke `/etc/fleetview`)**

Ganti setiap:
```yaml
    env_file:
      - /etc/fleetview/central.env
      - /etc/fleetview/secrets.env
```
Menjadi:
```yaml
    env_file:
      - ${FLEETVIEW_CENTRAL_ENV:-/etc/fleetview/central.env}
      - ${FLEETVIEW_SECRETS_ENV:-/etc/fleetview/secrets.env}
```
pada service `migrate` dan `api`. Default tetap path produksi asli — override hanya aktif kalau variabel di-set (seperti di README Step 1).

- [ ] **Step 4: Jalankan ulang Step 2 dari nol untuk memastikan README benar-benar akurat setelah Step 3**

Run seluruh blok bash di README dari direktori bersih (`docker compose down -v` dulu bila ada sisa dari task sebelumnya).
Expected: `curl -sf http://localhost:8080/api/health` mengembalikan HTTP 200, dashboard bisa dibuka di `http://localhost:8080` dan bisa login dengan `operator` setelah set password interaktif.

- [ ] **Step 5: Bersihkan**

```bash
docker compose -f docker-compose.prod.yml down -v
rm -rf /tmp/fleetview-central-try
```

- [ ] **Step 6: Commit**

```bash
git add README.md docker-compose.prod.yml
git commit -m "docs: tambah langkah coba central via Docker di README.md"
```

---

## Self-Review

**Spec coverage:**
- Dockerfile api → Task 1. ✓
- Dockerfile web/proxy + config nginx (perbaikan `location /`) → Task 2. ✓
- docker-compose.prod.yml lengkap (postgres, influxdb, migrate, api, proxy, certbot) → Task 3–5. ✓
- Bind mount data + ownership → Task 3 (compose), Task 7 (`install-central.sh` chown). ✓
- TLS webroot + renewal timer → Task 5. ✓
- Backup/diskwatch adaptasi → Task 6. ✓
- Rollback/versioning via image tag → disebutkan di Task 8 Step 8 (`FLEETVIEW_IMAGE_TAG`/`VERSION` build-arg); *catatan: build-arg `VERSION` dirujuk di dokumentasi tapi Dockerfile Task 1 belum mendeklarasikan `ARG VERSION` — ini gap kecil, ditambahkan sebagai Step tambahan di bawah.*
- Dokumen 03-central-deployment.md, 00-menjalankan.md, central/README.md → Task 8–9. ✓
- Build context root repo (monorepo) → Task 1 & 2 Dockerfile `COPY` dari root, dikonfirmasi lewat `context: .` di semua definisi `build:` compose. ✓
- README.md langkah coba → Task 10. ✓ (item baru dari permintaan pengguna di tengah sesi)

**Gap ditemukan & diperbaiki:** `ARG VERSION` belum dideklarasikan di Dockerfile meski dirujuk di dokumentasi upgrade (Task 8 Step 8). Tambahan step:

- [ ] **Task 1 — Step tambahan: Tambah `ARG VERSION` ke `central/api/Dockerfile`**

Tambahkan di stage `runtime`, sebelum `CMD`:
```dockerfile
ARG VERSION=dev
ENV FLEETVIEW_VERSION=${VERSION}
LABEL org.opencontainers.image.version=${VERSION}
```

Run: `docker build -f central/api/Dockerfile --build-arg VERSION=v1.2.3 -t fleetview-central-api:v1.2.3 .`
Expected: `docker inspect fleetview-central-api:v1.2.3 --format '{{.Config.Labels}}'` menunjukkan `version:v1.2.3`.

Commit ini digabung ke commit Task 1 (amend sebelum lanjut Task 2 kalau dikerjakan berurutan; kalau Task 1 sudah lama selesai, commit terpisah: `git commit -m "feat: tambah ARG VERSION untuk labeling image central-api"`).

**Placeholder scan:** tidak ada `TBD`/`TODO` tersisa di task manapun — satu-satunya catatan terbuka (uid image resmi di Task 7) sudah punya step verifikasi eksplisit (Step 2), bukan asumsi dibiarkan.

**Type/nama konsisten:** nama service compose (`postgres`, `influxdb`, `migrate`, `api`, `proxy`, `certbot`) dipakai identik di semua task 3–10. Nama file env (`central.env`, `secrets.env`) konsisten dari spec sampai Task 10. Path bind mount (`/var/lib/fleetview/{postgres,influx}-data`) konsisten Task 3, 6, 7.
