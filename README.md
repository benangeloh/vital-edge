# FleetView: Monitoring Armada Kapal SPIL

Platform telemetry untuk armada 70+ kapal. Data sensor panel LP-A dibaca di
kapal, **disimpan di kapal dulu**, lalu dikirim ke server pusat dan ditampilkan
di dashboard. Internet boleh putus, data tidak hilang.

**Dokumen ini adalah panduan utama**: cara memasang server pusat, menambah
kapal, dan memasangnya di kapal.

| Saya ingin... | Ke bagian |
|---|---|
| Memahami gambaran besar | [Gambaran besar](#0-gambaran-besar) |
| Tahu skrip mana yang dijalankan | [Skrip apa saja yang dipakai?](#skrip-apa-saja-yang-dipakai-baca-ini-dulu) |
| Memasang server pusat | [A. Deploy server pusat](#a-deploy-server-pusat-sekali) |
| Menambah kapal baru | [B. Tambah kapal di dashboard](#b-tambah-kapal-di-dashboard) |
| Memasang di kapal | [C. Pasang di kapal](#c-pasang-di-kapal) |
| Memastikan kapal sudah benar | [D. Verifikasi dari darat](#d-verifikasi-dari-darat) |
| Mencetak daftar periksa | [E. Daftar periksa per kapal](#e-daftar-periksa-per-kapal-cetak-dan-centang) |
| Mencoba di laptop, atau jadi developer | [docs/TENTANG-PROYEK.md](docs/TENTANG-PROYEK.md) |

Panduan satu halaman dari nol sampai data kapal terlihat di dashboard. Ikuti
urutannya. Setiap bagian punya kotak **Selesai bila** yang harus terpenuhi
sebelum lanjut, supaya tidak ada langkah yang terlewat.

Untuk alasan desain dan rincian teknis, tiap bagian menunjuk ke dokumen
aslinya di `docs/operations/`.

---

## 0. Gambaran besar

```
            KAPAL (ada 70+, masing-masing)                        SERVER PUSAT (satu)
 ┌──────────────────────────────────────────────┐          ┌──────────────────────────────┐
 │ Panel LP-A  ──Modbus TCP:502──>  Komputer    │          │  nginx (HTTPS)               │
 │ (sensor)                         kapal       │  internet│    │                         │
 │                                  · Edge Agent│ ───────> │  API central                 │
 │                                  · InfluxDB  │  (boleh  │    ├─ PostgreSQL (kapal,     │
 │                                    lokal     │  putus)  │    │   akun, status sinkron) │
 │                                  · Edge      │          │    └─ InfluxDB (data sensor  │
 │                                    Console   │          │        seluruh kapal)        │
 └──────────────────────────────────────────────┘          │  Dashboard operator          │
   data disimpan di kapal dulu, dikirim kemudian           └──────────────────────────────┘
```

Tiga pekerjaan terpisah:

| # | Pekerjaan | Siapa | Berapa kali | Di mana |
|---|---|---|---|---|
| **A** | Deploy server pusat | admin sistem | sekali | server pusat |
| **B** | Tambah kapal di dashboard | admin atau operator | tiap kapal baru | browser |
| **C** | Pasang di kapal | teknisi | tiap kapal baru | komputer kapal, panel LP-A |

Urutan wajib: **A, lalu B, lalu C**. Kredensial kapal hanya bisa diterbitkan
oleh central, jadi kapal selalu didaftarkan dulu.

Dua hal yang sering membingungkan:

- Pusat dan kapal **sama-sama punya InfluxDB**, fungsinya beda. Di kapal ia
  penyangga data lokal. Di pusat ia penyimpan data seluruh armada yang dibaca
  dashboard.
- Data sensor **tidak otomatis terbaca** dari panel. Tiap sensor butuh satu
  baris salin (`MOV`) di program panel. Lihat bagian C4.

---

## Skrip apa saja yang dipakai? (baca ini dulu)

Ada **dua jenis skrip untuk kapal**, dan keduanya sering tertukar:

| Skrip | Dari mana | Sama untuk semua kapal? | Berisi rahasia? | Siapa yang menjalankan |
|---|---|---|---|---|
| **`pasang-fleetview-SHIP-xxx.sh`** | **Diunduh dari dashboard** saat menambah kapal (bagian B) | **Tidak.** Dibuat khusus per kapal | **Ya**, kunci kapal itu | **Anda** di komputer kapal, satu perintah |
| **`install.sh`** | Ada di dalam kode Edge (`edge/deploy/scripts/`) | **Ya**, satu berkas yang sama | Tidak | **Tidak dijalankan manual.** Dipanggil otomatis oleh skrip di atas |

Jadi alurnya:

```
Dashboard ── Tambah kapal ──> unduh pasang-fleetview-SHIP-049.sh   (berbeda per kapal)
                                        │  dijalankan di komputer kapal
                                        ▼
                          mengambil kode Edge ──> menjalankan install.sh   (sama untuk semua kapal)
                                        ▼
                          mengatur panel LP-A, lalu mengirim kunci kapal ke pusat
```

Yang berbeda antar kapal hanya isi skrip yang diunduh: kode kapal, kunci,
alamat pusat, dan IP panel. `install.sh` tidak perlu Anda sentuh. Skrip lain
di `deploy/scripts/` (`install-central.sh`, `certbot-init.sh`) hanya untuk
**server pusat** dan tidak dipakai di kapal.

---

## A. Deploy server pusat (sekali)

### A1. Siapkan

| Kebutuhan | Nilai |
|---|---|
| Server | Debian 12 atau Ubuntu 22.04 LTS, 8 inti CPU, 32 GB RAM |
| Disk | 100 GB SSD untuk sistem, **4 TB NVMe** untuk data (perhitungan di [03](docs/operations/03-central-deployment.md)) |
| Jaringan | IP publik tetap, port **80 dan 443** terbuka |
| Domain | satu nama domain, misalnya `fleetview.spil.co.id`, **record A sudah mengarah ke IP server** |
| Email | untuk sertifikat HTTPS gratis (Let's Encrypt) |

### A2. Pasang Docker

Ikuti [panduan resmi Docker Engine](https://docs.docker.com/engine/install/)
untuk distro server, termasuk Compose plugin. Cek:

```bash
docker --version && docker compose version
```

### A3. Ambil kode

```bash
sudo mkdir -p /opt/fleetview/src
sudo git clone <url-repo> /opt/fleetview/src
cd /opt/fleetview/src
sudo git checkout <tag-rilis>
```

Pakai **tag rilis**, bukan `main`, supaya versi yang berjalan jelas dan bisa
dikembalikan.

### A4. Jalankan skrip persiapan

```bash
cd /opt/fleetview/src
sudo ./deploy/scripts/install-central.sh <domain> <email>
```

Skrip ini membuat folder data, password acak untuk Postgres dan InfluxDB di
`/etc/fleetview/secrets/`, pengguna `fleetview` untuk cadangan, dan berkas
`.env`.

### A5. Atur retensi data (sebelum nyala pertama)

Buka `.env` di root repo dan periksa:

```
INFLUXDB_INIT_RETENTION=365d
```

Retensi **harus lebih panjang** dari skenario kapal offline terlama yang mau
didukung. InfluxDB menolak data yang lebih tua dari retensi, dan penolakan itu
permanen. Ubah **sebelum** `up` pertama; setelah bucket dibuat, mengubahnya
butuh perintah `influx bucket update`.

### A6. Tulis konfigurasi aplikasi (dengan tangan, sekali)

Dua berkas ini **tidak dibuat otomatis**, karena isinya nilai produksi.

```bash
sudo install -d -o root -g fleetview -m 0750 /etc/fleetview

sudo tee /etc/fleetview/central.env >/dev/null <<'ENV'
FLEETVIEW_ENVIRONMENT=production
FLEETVIEW_LOG_FORMAT=json
FLEETVIEW_HOST=0.0.0.0
FLEETVIEW_PORT=8000
FLEETVIEW_INFLUX_URL=http://influxdb:8086
FLEETVIEW_INFLUX_ORG=fleetview
FLEETVIEW_INFLUX_BUCKET=telemetry
FLEETVIEW_CORS_ORIGINS=["https://<domain>"]
FLEETVIEW_TRUST_PROXY_HEADERS=true
ENV

sudo tee /etc/fleetview/secrets.env >/dev/null <<ENV
FLEETVIEW_POSTGRES_DSN=postgresql+asyncpg://fleetview:$(sudo cat /etc/fleetview/secrets/postgres_password.txt)@postgres:5432/fleetview
FLEETVIEW_INFLUX_TOKEN=$(sudo cat /etc/fleetview/secrets/influx_token.txt)
FLEETVIEW_JWT_SECRET=$(openssl rand -hex 32)
ENV
sudo chmod 0600 /etc/fleetview/secrets.env
sudo chown root:fleetview /etc/fleetview/secrets.env
```

Ganti `<domain>` dengan domain asli. Password dan token **harus diambil dari
berkas di `/etc/fleetview/secrets/`** seperti di atas, jangan dibuat baru.
Nilai baru tidak cocok dengan database yang sudah diinisialisasi.

### A7. Nyalakan database dan API

```bash
cd /opt/fleetview/src
docker compose -f docker-compose.prod.yml up -d postgres influxdb migrate api
docker compose -f docker-compose.prod.yml ps
```

`migrate` harus berstatus `Exited (0)`, dan `postgres`, `influxdb`, `api`
harus `healthy`. Kalau tidak:
`docker compose -f docker-compose.prod.yml logs migrate api`.

### A8. Pasang HTTPS dan nyalakan proxy

```bash
sudo ./deploy/scripts/certbot-init.sh <domain> <email>
docker compose -f docker-compose.prod.yml up -d

sudo cp deploy/systemd/fleetview-certbot-renew.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fleetview-certbot-renew.timer
```

### A9. Cadangan dan pemantau disk

```bash
sudo cp deploy/systemd/fleetview-backup.* deploy/systemd/fleetview-diskwatch.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fleetview-backup.timer fleetview-diskwatch.timer
```

Disk yang penuh menghentikan penerimaan data **seluruh armada sekaligus**, jadi
pemantau ini jangan dilewati.

### A10. Buat akun operator pertama

Tidak ada pendaftaran mandiri, dan itu disengaja.

```bash
docker compose -f docker-compose.prod.yml exec api \
  fleetview-admin user add --username <nama> --roles admin
```

Kata sandi diminta interaktif (jangan lewat `--password`, nanti tersimpan di
riwayat shell). Pakai minimal 12 karakter.

### A11. Verifikasi

```bash
curl -sf https://<domain>/api/health
```

Lalu buka `https://<domain>` di browser dan masuk dengan akun tadi.

> **Selesai bila:** halaman login terbuka lewat HTTPS tanpa peringatan
> sertifikat, Anda bisa masuk, dan Ringkasan Armada tampil kosong (belum ada
> kapal). Catat alamat `https://<domain>`, dipakai di bagian B.

Rincian, termasuk pembatasan laju, pemutakhiran, dan rollback:
[03 Deployment Central](docs/operations/03-central-deployment.md).

---

## B. Tambah kapal di dashboard

Dikerjakan sekali per kapal, oleh admin atau operator, sebelum teknisi
berangkat.

1. Masuk ke dashboard, buka menu **Kapal**, klik **Tambah kapal**.
2. Isi dua isian:
   - **Nama kapal**: nama resmi, misalnya `KM Sinar Jaya`.
   - **Kode kapal**: kode internal SPIL berbentuk `SHIP-nomor`. Sudah terisi
     dengan nomor berikutnya yang kosong. Boleh diubah, tetapi tidak boleh
     sama dengan kapal lain, dan **tidak bisa diganti** setelah kapal
     beroperasi (dipakai sebagai nama folder ekspor USB).
3. Klik **Daftarkan kapal**. Satu langkah ini membuat kapal, perangkat Edge,
   dan kredensialnya sekaligus.
4. Layar berikutnya menampilkan kredensial yang **hanya muncul sekali**. Di
   server hanya tersimpan hash-nya dan tidak bisa dibaca kembali.
   - Isi **Alamat pusat** dengan `https://<domain>` dari bagian A. Jangan
     biarkan `localhost`, karena kapal tidak bisa menjangkaunya.
   - Isi **Alamat IP panel LP-A104**, misalnya `192.168.100.100`. Tanyakan ke
     tim IoT. Bila dikosongkan, kapal berjalan dalam **mode simulasi** dan
     datanya bukan data asli.
   - Klik **Unduh skrip pemasangan**. Berkas bernama
     `pasang-fleetview-SHIP-xxx.sh`. **Inilah skrip yang nanti dijalankan di
     komputer kapal.** Isinya khusus untuk kapal yang baru didaftarkan ini,
     jadi tiap kapal punya skripnya sendiri.
5. Klik **Selesai**.

**Berkas skrip itu rahasia**: memuat kunci kapal. Kirim ke teknisi lewat
saluran aman (USB langsung, atau penyimpan rahasia yang sudah dipakai tim).
Jangan lewat chat atau email. Skrip menghapus dirinya setelah berhasil
dipasang.

Kalau skrip hilang sebelum dipakai, kredensial harus diterbitkan ulang (lihat
bagian F).

> **Selesai bila:** kapal muncul di Ringkasan Armada dengan status
> **Offline**. Itu normal, komputer kapalnya memang belum ada. Skrip sudah
> diserahkan ke teknisi, dan `client ID` (contoh `ship-049`) dicatat di
> inventaris. **Secret tidak dicatat di mana pun.**

---

## C. Pasang di kapal

### C1. Yang harus ada sebelum berangkat

Cek dulu di darat. Kesalahan kecil di kapal berarti naik-turun lagi.

- [ ] Komputer kapal: **Ubuntu, Debian, atau Raspberry Pi OS 64-bit**. Windows
      belum didukung.
- [ ] Disk data berupa **SSD**. Kartu SD habis dalam hitungan bulan karena
      penulisan tiap detik.
- [ ] Zona waktu diatur ke **UTC** (supaya log antar kapal bisa dibandingkan).
- [ ] Akses internet **saat pemasangan** (mengunduh kode dan InfluxDB). Kapal
      tanpa internet: lihat C3.
- [ ] Komputer kapal satu jaringan dengan panel LP-A dan bisa mengaksesnya.
- [ ] Daya lewat **UPS**, bukan langsung dari bus kapal.
- [ ] Skrip pemasangan dari bagian B ada di USB.
- [ ] IP panel LP-A dan hasil persiapan panel (C4) sudah ada.

### C2. Jalankan skrip

Di komputer kapal, salin **skrip yang diunduh dari dashboard untuk kapal ini**
(bukan skrip kapal lain, dan bukan `install.sh`) lalu:

```bash
sudo bash pasang-fleetview-SHIP-xxx.sh
```

Skrip berjalan dalam empat tahap dan mencetak kemajuannya:

| Tahap | Yang dikerjakan |
|---|---|
| 1/4 | Mengambil kode Edge (atau memakai salinan lokal) |
| 2/4 | Memasang Edge Agent dan InfluxDB lokal (beberapa menit) |
| 3/4 | Mengatur agar agent membaca panel LP-A di IP yang diisi |
| 4/4 | Mengirim kredensial ke pusat lewat Edge Console lokal. Kredensial diuji ke pusat dulu, dan nama serta ID kapal diambil otomatis |

Berhasil bila muncul **"Pemasangan selesai untuk ..."**. Bila gagal, pesannya
menjelaskan sebabnya; perbaiki lalu jalankan ulang skrip yang sama (aman
diulang).

Pesan yang paling sering:

| Pesan | Artinya |
|---|---|
| kode Edge tidak bisa diambil | Tidak ada internet, atau repositori tidak bisa diakses. Lihat C3 |
| Alamat ini ditulis https:// tetapi server menjawab dengan HTTP biasa | Alamat pusat salah satu huruf |
| central menolak kredensial ini | Skrip salah kapal, atau kredensial sudah dicabut. Terbitkan ulang |
| Tidak ada yang menjawab di ... | Alamat pusat salah, atau server pusat mati |

### C3. Kapal tanpa internet

Salin folder kode Edge (hasil `git clone`) ke komputer kapal lewat USB, lalu:

```bash
sudo FLEETVIEW_EDGE_SRC=/jalur/ke/kode bash pasang-fleetview-SHIP-xxx.sh
```

InfluxDB tetap perlu diunduh saat pemasangan. Untuk kapal yang benar-benar
terisolasi, siapkan dan uji pemasangan di darat lebih dulu, lalu bawa komputer
yang sudah jadi.

Bila repositori kode bersifat privat, komputer kapal harus punya akses ke
sana, atau pakai `FLEETVIEW_EDGE_REPO_URL=<alamat lain>`.

### C4. Siapkan panel LP-A (tim IoT)

Aturan utamanya: **PC hanya bisa membaca area UW dan UB lewat Ethernet.**
Nilai sensor mendarat di memori D, yang tidak terbaca. Tiap sensor butuh satu
baris `MOV` di program panel yang menyalinnya ke UW (blok **UW1000 dan
seterusnya**). Tanpa itu, dashboard menampilkan **0** padahal layar HMI normal.

Sekali per panel (atDesigner): CH2 protokol `UNIVERSAL`, tipe **TCP/IP**,
port **502**, IP tetap yang dicatat di inventaris kapal.

Tiap kali menambah sensor: Edge Console menyusun sendiri baris yang harus
ditempel (lihat C5), tim IoT menempelnya ke program Main sebelum `END`,
lalu download ke panel.

Panduan lengkap, termasuk hal yang tidak boleh dilakukan:
[10 SOP panel LP-A](docs/operations/10-sop-panel-lp-a.md).

### C5. Tambah sensor di Edge Console

1. Dari laptop di jaringan yang sama, buka
   `http://<ip-komputer-kapal>:8080/sensors`.
2. Pada **Tambah sensor**: pilih **jenis** (RPM mesin, Suhu air tawar, atau
   Tekanan oli), isi **nama sensor** (sudah disarankan, huruf kecil dan
   garis bawah), dan **alamat sumber di panel** (misalnya `D00160`, dari tabel
   tim IoT).
3. Simpan. Edge Console menampilkan kotak **Baris untuk ditempel ke atLogic**.
   Serahkan ke tim IoT untuk dimasukkan ke program panel.
4. Setelah panel di-download, nilainya muncul di **Pembacaan langsung**.
   Bandingkan dengan layar HMI. Bila berbeda, periksa **skala** (desimal HMI)
   dulu, baru curigai penyalinnya.

Nama sensor menjadi tag di InfluxDB. **Mengubahnya memutus riwayat data**,
jadi tetapkan benar dari awal. Satuan juga.

### C6. Cek di kapal

```bash
sudo fleetview-status
```

Yang dicari: layanan hidup, **polling bertambah** antara dua cuplikan, dan
jumlah data tertunda **turun** bila kapal punya koneksi. Kapal yang masih di
dermaga tanpa jaringan boleh menumpuk data tertunda; akan terkirim sendiri
begitu terhubung.

> **Selesai bila:** `fleetview-status` sehat, nilai di Edge Console sama
> dengan layar HMI, dan komputer kapal terpasang di tempatnya (kering,
> berventilasi, tidak kena getaran, daya lewat UPS, difoto untuk rujukan).

---

## D. Verifikasi dari darat

Kapal **belum dianggap terpasang** sebelum keempatnya terpenuhi.

1. **Online.** Di Ringkasan Armada, kapal berstatus **Online** (atau
   **Sinkron** sebentar bila masih mengirim data tertunda).
2. **Data masuk.** Buka detail kapal, tab **Logger**. Pilih rentang
   (1 jam, atau tanggal sendiri) dan pastikan baris data terbaru berumur
   hitungan detik. Filter per sensor bila perlu.
3. **Tidak ada celah.** Tab **Status kapal**, bagian Sinkronisasi:
   *Rentang belum lengkap* harus **Tidak ada**.
4. **Nilainya masuk akal.** Tidak bisa diotomatiskan dan paling sering
   dilewati. Bandingkan RPM, suhu, dan tekanan di Logger dengan layar panel di
   ruang mesin. Sensor yang salah dipetakan menampilkan angka yang tampak
   sempurna dan sepenuhnya keliru.

Amati **72 jam pertama**: data tertunda yang terus menumpuk atau status yang
bolak-balik berarti ada yang tidak beres.

> **Selesai bila:** keempatnya lolos. Catat di inventaris: kode kapal,
> `ship_id`, `client_id`, IP panel, tanggal pasang, versi agent.

---

## E. Daftar periksa per kapal (cetak dan centang)

```
KAPAL: ________________   KODE: SHIP-____   TANGGAL: __________

DI DARAT, SEBELUM BERANGKAT
[ ] Kapal didaftarkan di dashboard (bagian B)
[ ] Alamat pusat diisi https://<domain> (bukan localhost)
[ ] IP panel LP-A diisi
[ ] Skrip diunduh dan dibawa lewat saluran aman
[ ] client ID dicatat di inventaris (secret TIDAK dicatat)
[ ] Komputer kapal: OS 64-bit, SSD, UTC, UPS siap
[ ] Panel LP-A: CH2 TCP/IP port 502, IP tetap, baris MOV siap

DI KAPAL
[ ] Komputer kapal terpasang, daya via UPS, terhubung ke panel
[ ] sudo bash pasang-fleetview-... selesai tanpa galat
[ ] Sensor ditambah di Edge Console
[ ] Program panel di-download dengan baris MOV baru
[ ] Nilai di Edge Console sama dengan layar HMI
[ ] sudo fleetview-status sehat, polling bertambah
[ ] Difoto untuk rujukan

DARI DARAT
[ ] Status Online di dashboard
[ ] Tab Logger menampilkan data baru (umur detik)
[ ] Rentang belum lengkap: Tidak ada
[ ] Nilai dibandingkan dengan HMI, wajar
[ ] 72 jam pertama diamati
```

---

## F. Perawatan

**Kredensial hilang atau komputer kapal diganti.** Dashboard belum punya
layar untuk menerbitkan ulang. Dari server pusat:

```bash
cd /opt/fleetview/src
docker compose -f docker-compose.prod.yml exec api \
  fleetview-admin credential issue --device-id <device_id> --client-id <nama-baru>
```

Kredensial lama **tidak otomatis dicabut**, karena perangkat lama mungkin
masih menyetor data tertunda. Cabut setelah data tertunda habis:

```bash
docker compose -f docker-compose.prod.yml exec api fleetview-admin credential revoke <client-id-lama>
```

Lalu jalankan ulang skrip pemasangan versi baru di komputer kapal, atau isi
ulang lewat halaman setup di Edge Console (`http://<ip>:8080/setup`, PIN dari
`sudo fleetview-status`).

**Memutakhirkan central.** `git fetch`, `git checkout <tag baru>`, lalu
`docker compose -f docker-compose.prod.yml up -d --build`. **Catat revisi
skema** (`exec api alembic -c /app/alembic.ini current`) sebelum dan sesudah:
kode bisa dikembalikan, migrasi tidak. Lihat
[03 §8](docs/operations/03-central-deployment.md).

**Cadangan dan pemulihan.** [04 Backup](docs/operations/04-backup.md) dan
[05 Disaster Recovery](docs/operations/05-disaster-recovery.md). Dokumen 05
belum diperbarui untuk alur Docker; perlakukan perintah `systemctl` di
dalamnya sebagai kandidat revisi.

---

## G. Hal penting dan keamanan

- **Rahasia kapal hanya muncul sekali.** Simpan skrip dengan hati-hati.
- **Mode simulasi bukan data asli.** Kapal tanpa alamat panel mengirim data
  buatan yang tampak sehat sempurna di dashboard. Jangan biarkan kapal yang
  sudah beroperasi dalam mode itu.
- **Edge Console tidak punya login.** Halaman setup dilindungi PIN. Di halaman
  Log, kata sandi InfluxDB kapal sengaja ditulis langsung (bawaan
  `fleetview123`), dengan asumsi Console hanya terjangkau dari jaringan
  anjungan. Ganti dengan `FLEETVIEW_INFLUX_PASSWORD=... sudo -E` saat
  memasang bila jaringan kapal tidak sepenuhnya tepercaya.
- **Windows belum didukung** untuk komputer kapal.
- **Bila InfluxDB pusat penuh**, penerimaan data seluruh armada berhenti.
  Pantau disk (A9).

---

## H. Status verifikasi panduan ini

Ditulis dari kode dan dokumen yang ada. Yang **belum diuji di perangkat
sungguhan**, harap dicoba pada satu server dan satu kapal percobaan sebelum
dipakai massal:

| Hal | Status |
|---|---|
| Image Docker central terbangun | Pembangunan diuji lokal, hasil akhirnya belum tercatat |
| Menyalakan stack produksi penuh di server sungguhan (HTTPS, sertifikat) | Belum diuji |
| Skrip pemasangan penuh di Ubuntu, Debian, atau Raspberry Pi OS | Bagian pengiriman kredensial dan pengaturan config diuji; `apt`, `systemd`, dan instalasi InfluxDB belum |
| Pembacaan panel **LP-A070** | Uji lapangan sebelumnya pada LP-A104; LP-A070 belum (lihat bagian G di [10 SOP](docs/operations/10-sop-panel-lp-a.md)) |
| Skala sensor RPM dan suhu | Belum dicocokkan ke HMI; tekanan oli sudah |

---

## I. Untuk developer

Menjalankan semuanya di satu laptop (`make dev`), struktur kode, perintah
`make`, dan mencoba stack produksi central lewat Docker di laptop sebelum
deploy sungguhan: **[docs/TENTANG-PROYEK.md](docs/TENTANG-PROYEK.md)**.

Dokumentasi lain: [indeks dokumen](docs/README.md) ·
[Onboarding kapal](docs/operations/07-ship-onboarding.md) ·
[Provisioning komputer kapal](docs/operations/02-provisioning-raspberry-pi.md) ·
[Deployment central](docs/operations/03-central-deployment.md) ·
[Troubleshooting](docs/operations/09-troubleshooting.md) ·
[SOP panel LP-A](docs/operations/10-sop-panel-lp-a.md)
