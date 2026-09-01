# 03 — Deployment Central Platform

Central adalah satu server yang melayani 70+ kapal dan seluruh operator.

Ia **modular monolith**: modularitasnya ada di batas kode, bukan di batas
jaringan. Satu proses, satu unit systemd. Tidak ada service discovery, tidak ada
mesh, dan tidak ada kegagalan parsial antar modul yang harus ditangani.

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

## 1. Basis data

```bash
sudo apt-get install -y postgresql-15 nginx
sudo -u postgres createuser fleetview --pwprompt
sudo -u postgres createdb fleetview --owner fleetview
```

InfluxDB dipasang manual di sini — berbeda dari sisi kapal, yang sejak
`install.sh` terbaru memasang dan men-provisioning InfluxDB otomatis (lihat
[02 §4](02-provisioning-raspberry-pi.md#4-pasang-edge-agent)). Central tidak
memakai skrip yang sama karena retensinya berbeda dan tidak lewat proses setup
teknisi lapangan:

```bash
curl -s https://repos.influxdata.com/influxdata-archive.key \
  | gpg --dearmor | sudo tee /usr/share/keyrings/influxdata.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/influxdata.gpg] \
https://repos.influxdata.com/debian stable main" \
  | sudo tee /etc/apt/sources.list.d/influxdata.list
sudo apt-get update && sudo apt-get install -y influxdb2
sudo systemctl enable --now influxdb

influx setup --username fleetview --org fleetview --bucket telemetry \
  --retention 365d --force
```

**Retensi bucket bukan sekadar kebijakan penyimpanan.** InfluxDB menolak titik
yang lebih tua dari retensi dengan HTTP 422. Kapal yang kembali online setelah
offline panjang akan menyetor data lama, dan bila retensi terlalu pendek, data
itu ditolak permanen. Retensi harus lebih longgar daripada skenario offline
terburuk yang ingin didukung.

## 2. Pasang aplikasi

```bash
sudo mkdir -p /opt/fleetview/src
sudo git clone <url-repo> /opt/fleetview/src
cd /opt/fleetview/src && sudo git checkout <tag rilis>

sudo useradd --system --home /var/lib/fleetview --shell /usr/sbin/nologin fleetview
sudo install -d -o fleetview -g fleetview /var/lib/fleetview /var/log/fleetview
sudo install -d -o root -g fleetview -m 0750 /etc/fleetview

VERSION=$(git describe --tags --always)
sudo install -d /opt/fleetview/releases/$VERSION
sudo cp -r central shared deploy pyproject.toml /opt/fleetview/releases/$VERSION/
sudo python3 -m venv /opt/fleetview/releases/$VERSION/venv
sudo /opt/fleetview/releases/$VERSION/venv/bin/pip install -q \
  /opt/fleetview/releases/$VERSION/shared/common \
  /opt/fleetview/releases/$VERSION/shared/contracts \
  /opt/fleetview/releases/$VERSION/central/api
sudo ln -sfn /opt/fleetview/releases/$VERSION /opt/fleetview/current
sudo ln -sfn /opt/fleetview/current/venv /opt/fleetview/venv
```

## 3. Konfigurasi

Central tidak memakai berkas YAML — seluruh konfigurasinya environment variable.

```bash
sudo tee /etc/fleetview/central.env >/dev/null <<'ENV'
FLEETVIEW_ENVIRONMENT=production
FLEETVIEW_LOG_FORMAT=json
FLEETVIEW_HOST=127.0.0.1
FLEETVIEW_PORT=8000
FLEETVIEW_INFLUX_URL=http://127.0.0.1:8086
FLEETVIEW_INFLUX_ORG=fleetview
FLEETVIEW_INFLUX_BUCKET=telemetry
FLEETVIEW_CORS_ORIGINS=["https://fleetview.example.com"]
FLEETVIEW_TRUST_PROXY_HEADERS=true
ENV

sudo tee /etc/fleetview/secrets.env >/dev/null <<ENV
FLEETVIEW_POSTGRES_DSN=postgresql+asyncpg://fleetview:$(openssl rand -hex 16)@127.0.0.1:5432/fleetview
FLEETVIEW_INFLUX_TOKEN=<token influx>
FLEETVIEW_JWT_SECRET=$(openssl rand -hex 32)
ENV
sudo chmod 0600 /etc/fleetview/secrets.env
sudo chown root:fleetview /etc/fleetview/secrets.env
```

`FLEETVIEW_HOST=127.0.0.1` disengaja: aplikasi hanya mendengar di loopback, dan
nginx yang menghadap keluar. Semua TLS dan pembatasan laju ada di nginx.

`FLEETVIEW_TRUST_PROXY_HEADERS=true` **wajib** dinyalakan di belakang nginx —
dan hanya di belakang nginx. Tanpanya, setiap baris audit log akan mencatat
`127.0.0.1` dan tidak bisa dipakai menyelidiki apa pun. Dinyalakan **tanpa**
reverse proxy di depan berarti sebaliknya: siapa pun bisa memalsukan alamatnya
sendiri lewat header `X-Forwarded-For`.

Agent akan menolak start bila config produksi punya lubang — `jwt_secret` kosong
atau terlalu pendek, `influx_token` kosong, `postgres_dsn` masih menunjuk
localhost, atau `log_format` bukan json. Gagal cepat di sini jauh lebih baik
daripada berjalan setengah aman.

## 4. Migrasi skema

```bash
cd /opt/fleetview/current/central/api
sudo -u fleetview /opt/fleetview/venv/bin/alembic upgrade head
sudo -u fleetview /opt/fleetview/venv/bin/alembic current   # catat revisinya
```

**Catat nomor revisi ini setiap kali deploy.** Rollback kode itu aman; rollback
melewati migrasi tidak. Lihat [05 — Disaster Recovery](05-disaster-recovery.md).

## 5. nginx dan TLS

```bash
sudo cp deploy/nginx/fleetview.conf /etc/nginx/sites-available/fleetview
sudo cp deploy/nginx/fleetview-proxy-headers.conf /etc/nginx/
sudo ln -sf /etc/nginx/sites-available/fleetview /etc/nginx/sites-enabled/
sudo nano /etc/nginx/sites-available/fleetview     # ganti server_name

sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d fleetview.example.com
sudo nginx -t && sudo systemctl reload nginx
```

Konfigurasi nginx memisahkan pembatasan laju menjadi tiga zona karena tiga bidang
API ini punya pola beban yang sangat berbeda: `ingest` longgar dengan burst besar
(70 kapal, jauh lebih deras saat sebuah kapal kembali membawa backlog),
`auth` ketat (sasaran percobaan kredensial), `query` sedang.

Yang dikembalikan saat limit terlampaui adalah **429, bukan 503**. Sync Engine di
kapal memperlakukan 429 sebagai bisa dicoba ulang; kode lain bisa membuatnya
menyerah dan mengarantina data yang sebenarnya baik-baik saja.

Perpanjangan sertifikat otomatis lewat timer certbot. Verifikasi sekarang, bukan
89 hari lagi:

```bash
sudo certbot renew --dry-run
```

## 6. Nyalakan

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fleetview-central
sudo systemctl enable --now fleetview-backup.timer fleetview-diskwatch.timer
```

## 6b. Operator pertama

Tanpa langkah ini dashboard tidak bisa dimasuki. Tidak ada endpoint pendaftaran
mandiri, dan itu disengaja: platform yang mengawasi 70 kapal tidak boleh punya
jalur pendaftaran terbuka.

```bash
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin user add \
  --username <nama> --roles admin
#   kata sandi diminta interaktif — jangan lewat --password di server produksi,
#   karena akan tersimpan di riwayat shell dan terlihat di daftar proses.

sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin user list
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

Ulangi langkah 2 (rilis baru bertanda versi), jalankan migrasi, lalu tukar
symlink dan restart:

```bash
sudo ln -sfn /opt/fleetview/releases/$VERSION /opt/fleetview/current
sudo ln -sfn /opt/fleetview/current/venv /opt/fleetview/venv
sudo systemctl restart fleetview-central
```

Central boleh mati sebentar. Kapal akan mencoba ulang sendiri dan tidak ada data
yang hilang — itu inti dari desain offline-first. Yang **tidak** boleh adalah
central berjalan dengan skema yang tidak cocok dengan kodenya.

Selalu jalankan pencadangan sebelum migrasi:

```bash
sudo systemctl start fleetview-backup.service
```
