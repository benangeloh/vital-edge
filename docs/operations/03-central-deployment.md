# 03 — Deployment Central Platform

Central adalah satu server yang melayani 70+ kapal dan seluruh operator.

Ia **modular monolith**: modularitasnya ada di batas kode, bukan di batas
jaringan. Aplikasinya sendiri tetap satu proses (satu container `api`) — tidak
ada service discovery atau mesh antar modul aplikasi, dan tidak ada kegagalan
parsial antar modul yang harus ditangani. Seluruh stack (PostgreSQL, InfluxDB,
job migrasi, `api`, proxy nginx) diorkestrasi sebagai satu unit lewat Docker
Compose (`docker-compose.prod.yml`), bukan systemd unit terpisah per komponen
seperti pada versi sebelum containerisasi.

---

## Kebutuhan server

| Bagian | Minimum | Alasan |
|---|---|---|
| CPU | 8 inti | Ingest 70 kapal + dekompresi + tulis Influx |
| RAM | 32 GB | InfluxDB memakai sebagian besarnya untuk indeks |
| Disk sistem | 100 GB SSD | OS, aplikasi, log |
| Disk data | **4 TB NVMe** | Lihat perhitungan di bawah |
| OS | Debian 12 / Ubuntu 22.04 LTS | |

**Perhitungan disk.** Terukur: 127 GB/hari line protocol untuk armada penuh
sebelum kompresi. InfluxDB TSM biasanya memampatkan telemetry numerik 8–15×,
sehingga sekitar 10 GB/hari tersimpan. Dengan retensi 365 hari itu ~3,6 TB.

Angka ini adalah yang paling perlu diverifikasi ulang setelah 30 hari produksi.
Rasio kompresi sangat bergantung pada isi data sungguhan, dan salah perkiraan di
sini adalah satu-satunya kegagalan yang menghentikan ingest untuk **seluruh
armada sekaligus**. Lihat bagian pemantauan disk di bawah.

---

## 1–2. Pasang Docker dan aplikasi

Sejak alur ini, seluruh stack (PostgreSQL, InfluxDB, aplikasi, proxy nginx)
berjalan sebagai container yang diorkestrasi oleh `docker-compose.prod.yml` —
tidak ada lagi `apt-get install postgresql`/`nginx`, tidak ada virtualenv
manual, dan tidak ada symlink rilis `/opt/fleetview/current`. Repo di-clone
langsung ke `/opt/fleetview/src` dan tag rilis yang di-checkout di situ **adalah**
rilis yang berjalan — versioning dan rollback dilakukan lewat tag image Docker
(`FLEETVIEW_IMAGE_TAG`, lihat §8), bukan lewat menukar symlink.

Prasyarat: Docker Engine + Compose plugin ([docs resmi](https://docs.docker.com/engine/install/)).

```bash
sudo mkdir -p /opt/fleetview/src
sudo git clone <url-repo> /opt/fleetview/src
cd /opt/fleetview/src && sudo git checkout <tag rilis>
sudo ./deploy/scripts/install-central.sh fleetview.example.com admin@example.com
```

`install-central.sh` (`deploy/scripts/install-central.sh`) melakukan bagian
yang aman diotomatisasi:

- Membuat direktori data `/var/lib/fleetview/{postgres,influx}-data` dengan
  ownership yang cocok untuk image resminya — uid `70` untuk
  `postgres:16-alpine`, uid `1000` untuk `influxdb:2.7-alpine` (keduanya
  berjalan non-root secara default).
- Membuat secret Postgres/Influx acak di `/etc/fleetview/secrets/*.txt`
  (dipakai lewat Docker secrets oleh service `postgres` dan `influxdb`).
- Menyalin `.env.prod.example` menjadi `.env` di root repo — berkas ini dibaca
  **Docker Compose sendiri** untuk interpolasi `${VAR}` di
  `docker-compose.prod.yml` (`FLEETVIEW_DATA_DIR`, `POSTGRES_USER`,
  `INFLUXDB_INIT_*`, `FLEETVIEW_IMAGE_TAG`). Ini **bukan** konfigurasi
  aplikasi FastAPI — lihat §3 untuk perbedaannya dengan `central.env`.

Yang **tidak** dilakukan skrip ini — sengaja, karena isinya nilai produksi
sungguhan yang harus ditulis tangan oleh operator: membuat
`/etc/fleetview/central.env`/`secrets.env` (§3), membangun image, atau
menyalakan stack (§4–§6). Skrip mencetak ringkasan langkah-langkah itu di
akhir jalannya.

PostgreSQL dan InfluxDB tidak lagi dipasang manual di host — keduanya
container (`postgres:16-alpine`, `influxdb:2.7-alpine`) yang dibawa naik lewat
`docker-compose.prod.yml`. Pembuatan user/database Postgres terjadi otomatis
lewat env var image resmi (`POSTGRES_USER`, `POSTGRES_DB`,
`POSTGRES_PASSWORD_FILE`, dari `.env` dan secret di atas). InfluxDB
diinisialisasi sekali secara otomatis (`DOCKER_INFLUXDB_INIT_MODE=setup`)
memakai org/bucket/retensi dari `.env` — default `fleetview`/`telemetry`/`365d`
(lihat `INFLUXDB_INIT_ORG`, `INFLUXDB_INIT_BUCKET`, `INFLUXDB_INIT_RETENTION`
di `.env.prod.example`). Ini beda mekanisme dari sisi kapal (yang men-Debian
InfluxDB lewat `install.sh`, lihat [02 §4](02-provisioning-raspberry-pi.md#4-pasang-edge-agent)),
tapi keduanya sama-sama otomatis sekarang — tidak ada lagi langkah `apt-get
install influxdb2` manual di central.

**Retensi bucket bukan sekadar kebijakan penyimpanan.** InfluxDB menolak titik
yang lebih tua dari retensi dengan HTTP 422. Kapal yang kembali online setelah
offline panjang akan menyetor data lama, dan bila retensi terlalu pendek, data
itu ditolak permanen. Retensi harus lebih longgar daripada skenario offline
terburuk yang ingin didukung — ubah `INFLUXDB_INIT_RETENTION` di `.env`
**sebelum** `up` pertama kali (perubahan setelah bucket dibuat butuh
`influx bucket update`, bukan sekadar mengubah env var).

## 3. Konfigurasi

Ada **dua berkas env yang terpisah dan tidak boleh dicampur**:

- **`.env`** (root repo, dibuat di §1–2 dari `.env.prod.example`) — dibaca
  Docker Compose sendiri untuk interpolasi `${VAR}` di YAML-nya. Isinya
  parameter orkestrasi: `FLEETVIEW_DATA_DIR`, `POSTGRES_USER`,
  `INFLUXDB_INIT_*`, `FLEETVIEW_IMAGE_TAG`.
- **`/etc/fleetview/central.env`** dan **`/etc/fleetview/secrets.env`** —
  konfigurasi aplikasi FastAPI itu sendiri, dibaca lewat `env_file:` service
  `api` dan `migrate` di `docker-compose.prod.yml`. Formatnya **tidak berubah**
  dari versi pra-Docker — tetap environment variable `FLEETVIEW_*`, bukan
  YAML — dan **tidak dibuat otomatis oleh skrip mana pun** (lihat §1–2);
  operator menulisnya tangan, sekali, di server:

```bash
sudo install -d -o root -g fleetview -m 0750 /etc/fleetview
sudo useradd --system --shell /usr/sbin/nologin fleetview || true

sudo tee /etc/fleetview/central.env >/dev/null <<'ENV'
FLEETVIEW_ENVIRONMENT=production
FLEETVIEW_LOG_FORMAT=json
FLEETVIEW_HOST=0.0.0.0
FLEETVIEW_PORT=8000
FLEETVIEW_INFLUX_URL=http://influxdb:8086
FLEETVIEW_INFLUX_ORG=fleetview
FLEETVIEW_INFLUX_BUCKET=telemetry
FLEETVIEW_CORS_ORIGINS=["https://fleetview.example.com"]
FLEETVIEW_TRUST_PROXY_HEADERS=true
ENV

sudo tee /etc/fleetview/secrets.env >/dev/null <<ENV
FLEETVIEW_POSTGRES_DSN=postgresql+asyncpg://fleetview:$(sudo cat /etc/fleetview/secrets/postgres_password.txt)@postgres:5432/fleetview
FLEETVIEW_INFLUX_TOKEN=$(sudo cat /etc/fleetview/secrets/influx_token.txt)
FLEETVIEW_JWT_SECRET=$(openssl rand -hex 32)
FLEETVIEW_CREDENTIAL_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
ENV
sudo chmod 0600 /etc/fleetview/secrets.env
sudo chown root:fleetview /etc/fleetview/secrets.env
```

**`FLEETVIEW_CREDENTIAL_KEY`** mengenkripsi rahasia kredensial kapal supaya skrip
pasang bisa diunduh ulang dari dashboard dengan kredensial yang sama. Buat sekali
dan **jangan diganti**: kunci yang berubah atau hilang membuat kredensial
tersimpan tidak terbaca (kapal yang sudah terpasang tetap bisa login; hanya
skripnya yang tidak bisa dibuka lagi, dan harus diterbitkan ulang). Berkas ini
tidak ikut dicadangkan, jadi simpan salinan kuncinya di penyimpan rahasia tim.
Kosong berarti fitur itu mati dan kredensial baru hanya tampil sekali.

`FLEETVIEW_POSTGRES_DSN` dan `FLEETVIEW_INFLUX_TOKEN` di atas **mengambil nilai
dari** `/etc/fleetview/secrets/postgres_password.txt` dan `influx_token.txt` —
berkas yang sama yang dibuat `install-central.sh` di §1–2 dan dipakai lewat
Docker secret oleh container `postgres`/`influxdb`. Nilainya harus sama persis;
membangkitkan password baru di sini (alih-alih membaca yang sudah ada) membuat
`api` gagal autentikasi ke container yang sudah terlanjur diinisialisasi
dengan password lain.

`FLEETVIEW_HOST=0.0.0.0` (dulu `127.0.0.1`) disengaja: di dalam container,
loopback (`127.0.0.1`) hanya menjangkau proses itu sendiri, bukan container
lain. Proxy nginx (container `proxy`) menjangkau `api` lewat jaringan Docker
internal (nama service, port kontainer), bukan loopback host — beda dari
setup systemd lama di mana nginx dan aplikasi berbagi network stack host yang
sama. Alasan yang sama berlaku untuk `FLEETVIEW_INFLUX_URL` dan
`FLEETVIEW_POSTGRES_DSN` di atas: host-nya adalah nama service Compose
(`influxdb`, `postgres`), bukan `127.0.0.1`. TLS dan pembatasan laju tetap
seluruhnya berada di proxy, tidak berubah.

`FLEETVIEW_TRUST_PROXY_HEADERS=true` **wajib** dinyalakan di belakang proxy —
dan hanya di belakang proxy. Tanpanya, setiap baris audit log akan mencatat
alamat IP internal container proxy dan tidak bisa dipakai menyelidiki apa pun.
Dinyalakan **tanpa** reverse proxy di depan berarti sebaliknya: siapa pun bisa
memalsukan alamatnya sendiri lewat header `X-Forwarded-For`.

Agent akan menolak start bila config produksi punya lubang — `jwt_secret` kosong
atau terlalu pendek, `influx_token` kosong, `postgres_dsn` masih menunjuk
localhost, atau `log_format` bukan json. Gagal cepat di sini jauh lebih baik
daripada berjalan setengah aman.

## 4. Migrasi skema

Otomatis: service `migrate` di `docker-compose.prod.yml` menjalankan
`alembic upgrade head` sekali setiap `docker compose up`, dan `api` menunggu
sampai `migrate` selesai sukses (`service_completed_successfully`) sebelum
container-nya start.

```bash
docker compose -f docker-compose.prod.yml logs migrate
docker compose -f docker-compose.prod.yml ps migrate     # harus "Exited (0)"

# Catat revisi skema saat ini — sama pentingnya seperti sebelumnya:
docker compose -f docker-compose.prod.yml exec api \
  alembic -c /app/alembic.ini current
```

**Catat nomor revisi ini setiap kali deploy.** Rollback kode itu aman; rollback
melewati migrasi tidak. Lihat [05 — Disaster Recovery](05-disaster-recovery.md)
— dokumen itu belum diperbarui untuk alur Docker ini pada saat penulisan;
perlakukan path/perintah `systemctl`/`/opt/fleetview/current` di sana sebagai
kandidat untuk diperbarui, bukan sebagai kebenaran saat ini.

## 5. nginx dan TLS

nginx sekarang berjalan sebagai container `proxy`, dibangun dari
`central/web/Dockerfile` dengan konfigurasi dari `central/web/nginx/fleetview.conf`
dan `central/web/nginx/fleetview-proxy-headers.conf` yang ikut ter-bake ke
dalam image — bukan lagi dipasang lewat `apt-get install nginx` di host dan
bukan lagi berkas di `deploy/nginx/` (berkas itu peninggalan alur lama,
tidak lagi dipakai oleh image `proxy`).

Setup sertifikat awal lewat `deploy/scripts/certbot-init.sh`:

```bash
sudo ./deploy/scripts/certbot-init.sh fleetview.example.com admin@example.com
```

`fleetview.conf` tidak lagi diubah oleh skrip ini — kedua server block di
sana sudah memakai `server_name _;` (catch-all), yang melayani host
satu-domain ini identik dengan menyebut domainnya secara eksplisit, jadi tidak
ada substitusi teks yang diperlukan (dan tidak ada risiko konflik dengan `git
checkout <tag>` saat upgrade). Sebelum menyalakan `proxy`, skrip membuat
sertifikat self-signed sekali-pakai di path yang sama dengan yang
di-hardcode di `ssl_certificate`/`ssl_certificate_key` — TANPA ini nginx
menolak start sama sekali karena berkas sertifikatnya belum ada, port 80 tidak
pernah terbuka, dan ACME challenge di bawah tidak pernah bisa dijawab (langkah
ini no-op bila sertifikat asli sudah ada). Skrip lalu menyalakan container
`proxy`, meminta sertifikat asli lewat HTTP-01 webroot challenge (container
`certbot`, berbagi volume `acme-challenge` dengan `proxy` lewat
`/var/www/acme-challenge`) dengan nama sertifikat tetap `fleetview`
(`--cert-name fleetview` — cocok dengan path `ssl_certificate` yang
di-hardcode di `fleetview.conf`, menimpa sertifikat self-signed di atas), lalu
me-reload nginx di dalam container.

Konfigurasi nginx memisahkan pembatasan laju menjadi tiga zona karena tiga bidang
API ini punya pola beban yang sangat berbeda: `ingest` longgar dengan burst besar
(70 kapal, jauh lebih deras saat sebuah kapal kembali membawa backlog),
`auth` ketat (sasaran percobaan kredensial), `query` sedang.

Yang dikembalikan saat limit terlampaui adalah **429, bukan 503**. Sync Engine di
kapal memperlakukan 429 sebagai bisa dicoba ulang; kode lain bisa membuatnya
menyerah dan mengarantina data yang sebenarnya baik-baik saja.

Perpanjangan sertifikat otomatis lewat `fleetview-certbot-renew.timer`
(systemd timer di host yang memanggil `deploy/scripts/certbot-renew.sh`, yang
pada gilirannya menjalankan `docker compose run --rm certbot renew` lalu
reload nginx di container) — bukan lagi cron bawaan paket `certbot` host:

```bash
sudo cp deploy/systemd/fleetview-certbot-renew.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fleetview-certbot-renew.timer
```

Verifikasi sekarang, bukan 89 hari lagi:

```bash
docker compose -f docker-compose.prod.yml run --rm certbot renew --dry-run
```

## 6. Nyalakan

```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps    # semua "healthy"
```

Service `migrate` tidak punya status "healthy" — job sekali-jalan (menjalankan
migrasi lalu keluar) dan akan tampil `Exited (0)` di `ps`. Itu normal. Service
`certbot` punya `profiles: ["tools"]`, jadi ia **tidak ikut dibuat sama
sekali** oleh `up -d` polos — tidak akan muncul di `ps` di atas. Ia hanya
muncul saat dipanggil eksplisit lewat `docker compose run --rm certbot ...`
(lihat §5), yang sudah jadi cara `certbot-init.sh`/`certbot-renew.sh`
memanggilnya. Yang harus "healthy" dari `up -d` polos adalah `postgres`,
`influxdb`, `api`, dan `proxy`.

Timer periodik lain (cadangan, pemantauan disk) tetap lewat systemd di host,
memanggil skrip yang sekarang men-`docker compose exec`/`cp` ke dalam stack
(`deploy/scripts/backup-central.sh`, `deploy/scripts/diskwatch.sh`):

```bash
sudo cp deploy/systemd/fleetview-backup.* deploy/systemd/fleetview-diskwatch.* \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fleetview-backup.timer fleetview-diskwatch.timer
```

`install-central.sh` sudah menambahkan user `fleetview` ke grup `docker` (dan
membuat `/var/backups/fleetview` dengan ownership yang sesuai) supaya
`backup-central.sh` bisa memanggil `docker compose exec` tanpa root penuh.
Keanggotaan grup `docker` secara efektif setara akses root di host — trade-off
keamanan yang disengaja demi kesederhanaan operasional, bukan celah yang
terlewat.

`fleetview-central.service` (unit systemd lama yang menjalankan aplikasi
langsung di host lewat venv) tidak lagi dipakai sama sekali — proses `api`
sekarang dikelola oleh Docker Compose (`restart: unless-stopped`), bukan
systemd. Jangan salin unit itu.

## 6b. Operator pertama

Tanpa langkah ini dashboard tidak bisa dimasuki. Tidak ada endpoint pendaftaran
mandiri, dan itu disengaja: platform yang mengawasi 70 kapal tidak boleh punya
jalur pendaftaran terbuka.

```bash
docker compose -f docker-compose.prod.yml exec api \
  fleetview-admin user add --username <nama> --roles admin
#   kata sandi diminta interaktif — jangan lewat --password di server produksi,
#   karena akan tersimpan di riwayat shell dan terlihat di daftar proses.

docker compose -f docker-compose.prod.yml exec api fleetview-admin user list
```

## 7. Verifikasi

```bash
curl -sf https://fleetview.example.com/api/health | jq
curl -sf https://fleetview.example.com/api/v1/health -H "Authorization: Bearer <token>" | jq
```

Ada dua endpoint kesehatan dan perbedaannya disengaja:

- `/api/health` — tanpa dependency. Selalu menjawab selama proses hidup. Ini yang
  dipakai load balancer dan pemantau uptime.
- `/api/v1/health` — memeriksa PostgreSQL dan InfluxDB sungguhan, mengembalikan
  `healthy` atau `degraded`. Ini yang dipakai manusia dan alerting.

Kalau keduanya digabung, kegagalan basis data akan membuat load balancer
mengeluarkan server dari rotasi — padahal tidak ada server lain untuk menggantikan,
dan hasilnya hanya menghilangkan satu-satunya jalan masuk untuk mendiagnosis.

Verifikasi TLS dan pembatasan laju:

```bash
curl -sI http://fleetview.example.com | head -1          # -> 301
for i in $(seq 1 20); do
  curl -s -o /dev/null -w "%{http_code} " \
    -X POST https://fleetview.example.com/api/v1/auth/login \
    -H 'Content-Type: application/json' -d '{"username":"x","password":"y"}'
done; echo    # harus muncul 429 setelah beberapa percobaan
```

## 8. Pemutakhiran

Selalu jalankan pencadangan sebelum migrasi — cadangkan dulu, selalu:

```bash
sudo systemctl start fleetview-backup.service
```

Lalu ambil rilis baru dan bangun ulang image dengan tag versi baru. Versioning
sekarang lewat tag image Docker (`FLEETVIEW_IMAGE_TAG` di `.env`, lihat §1–2
dan §3), bukan lewat menukar symlink `/opt/fleetview/current`:

```bash
cd /opt/fleetview/src
sudo git fetch --tags && sudo git checkout <tag baru>
VERSION=$(git describe --tags --always)

# set FLEETVIEW_IMAGE_TAG=<tag baru> di .env — lewat sed atau edit langsung:
sudo sed -i "s/^FLEETVIEW_IMAGE_TAG=.*/FLEETVIEW_IMAGE_TAG=${VERSION}/" .env

docker compose -f docker-compose.prod.yml build --build-arg VERSION="${VERSION}"
docker compose -f docker-compose.prod.yml up -d
```

`--build-arg VERSION` mengisi `ARG VERSION` di `central/api/Dockerfile`, yang
melabeli image (`LABEL org.opencontainers.image.version`) dan mengisi
`FLEETVIEW_VERSION` di dalam container — dipakai untuk keperluan diagnosa,
terpisah dari `FLEETVIEW_IMAGE_TAG` yang menentukan tag image mana yang
sebenarnya dijalankan Compose.

Urutan **cadangkan → migrasi (otomatis lewat job `migrate` sebelum `api`
start, §4) → tukar rilis** tetap sama persis prinsipnya dengan sebelumnya —
hanya mekanismenya yang berubah, dari symlink `current` + `systemctl restart`
menjadi tag image + `docker compose up -d`.

Central boleh mati sebentar. Kapal akan mencoba ulang sendiri dan tidak ada data
yang hilang — itu inti dari desain offline-first. Yang **tidak** boleh adalah
central berjalan dengan skema yang tidak cocok dengan kodenya. Catat revisi
skema (§4) setiap kali deploy — lihat [05 — Disaster Recovery](05-disaster-recovery.md).
