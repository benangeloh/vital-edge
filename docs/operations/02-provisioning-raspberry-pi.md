# 02 — Provisioning Raspberry Pi

Menyiapkan satu Raspberry Pi dari nol sampai siap dikirim ke kapal.

> Ini **Tahap 2** dari empat tahap onboarding kapal baru. Alur lengkapnya —
> mulai dari mendaftarkan kapal di dashboard sampai datanya terlihat di central
> — ada di [07 — Onboarding Kapal Baru](07-ship-onboarding.md). Kalau Anda
> sampai di sini tanpa membaca dokumen itu, baca dulu: Pi ini butuh `ship_id`
> dan kredensial yang diterbitkan di sana.

**Kerjakan seluruhnya di darat.** Kapal bukan tempat untuk instalasi pertama:
tidak ada internet yang bisa diandalkan, dan kesalahan kecil berarti perangkat
harus dibawa turun lagi.

Perkiraan waktu: 45 menit per unit, sebagian besar menunggu.

---

## Kebutuhan

| Bagian | Spesifikasi | Alasan |
|---|---|---|
| Board | Raspberry Pi 4B, RAM 4 GB | Puncak memori agent ~167 MB; sisanya untuk InfluxDB |
| Boot | SD card 32 GB kelas A2 | Hanya sistem operasi |
| Data | **SSD USB 128 GB** | Wajib. Lihat catatan di bawah |
| Daya | UPS/DC-UPS dengan baterai | Kapal sering kehilangan daya mendadak |
| OS | Raspberry Pi OS Lite 64-bit (Bookworm) | Python 3.11, sama dengan lingkungan pengembangan |

### Kenapa SSD, dan kenapa tidak perlu besar

Terukur: satu kapal butuh sekitar **37 GB** — InfluxDB 90 hari (20 GB), outbox
skenario offline terburuk 30 hari (6,5 GB), dan OS beserta aplikasi (~10 GB).
**128 GB sudah sangat lapang; 500 GB mubazir.** Yang menentukan bukan
kapasitasnya, melainkan jenis medianya.

**Hard disk mekanis (HDD) tidak boleh dipakai di kapal.** Alasan utamanya bukan
kecepatan, melainkan **getaran**: mesin induk, poros baling-baling, dan hantaman
gelombang bergetar terus-menerus, sementara head HDD melayang beberapa nanometer
di atas piringan yang berputar. Itu lingkungan yang justru paling mematikan bagi
HDD. Ditambah lagi listrik kapal sering putus mendadak, dan HDD yang mati di
tengah penulisan berisiko head contact — kerusakan fisik, bukan sekadar berkas
rusak.

Untuk 100–500 GB, HDD juga tidak lebih murah: kapasitas sekecil itu praktis
sudah ditinggalkan pasar HDD, sementara SSD SATA 128–256 GB sangat terjangkau.
Karena kebutuhannya hanya 37 GB, memilih SSD kecil justru **lebih murah**
daripada HDD besar — sekaligus menyelesaikan soal getaran.

**SD card hanya untuk boot.** Outbox melakukan fsync setiap detik — sekitar
86.400 kali sehari. Tulisan kecil yang sering di-sync adalah pola terburuk untuk
kartu SD karena erase block-nya besar, sehingga write amplification-nya tinggi
dan kartu konsumer habis dalam hitungan bulan. Seluruh `/var/lib/fleetview`
harus berada di SSD.

Pilihan pemasangan pada Raspberry Pi 5, berurut dari yang paling dianjurkan:

| Cara | Catatan |
|---|---|
| **NVMe HAT (PCIe)** | Paling rapi dan paling andal. Pi 5 punya slot PCIe. |
| **SSD USB 3.0** | Bekerja baik. Hindari adapter USB-SATA murah — sebagian punya masalah UAS di Pi. |
| microSD industrial/high-endurance | Hanya untuk unit uji coba. Jauh lebih tahan daripada kartu konsumer, tetap kalah dari SSD. |
| microSD konsumer | **Jangan** untuk kapal yang beroperasi. |

---

## 1. Sistem operasi

Flash Raspberry Pi OS Lite 64-bit. Di Raspberry Pi Imager, buka pengaturan lanjutan
dan setel: hostname `fleetview-<slug kapal>` (misalnya `fleetview-ship-023`),
pengguna `fleetview`, SSH dengan kunci publik saja, dan zona waktu UTC.

**Login kata sandi harus dimatikan.** Perangkat ini akan berada di jaringan kapal
yang tidak kita kendalikan.

**Zona waktu UTC, tanpa kecuali.** Seluruh sistem memakai UTC dari ujung ke ujung.
Pi yang memakai zona waktu lokal akan menghasilkan telemetry yang tampak bergeser
beberapa jam di dashboard, dan itu jenis kesalahan yang butuh berhari-hari untuk
dilacak.

## 2. Sistem berkas dan dasar

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y python3-venv python3-pip git sqlite3 chrony

# SSD dipasang permanen berdasarkan UUID, bukan /dev/sda1: urutan penamaan
# perangkat USB bisa berubah setelah reboot, dan salah mount berarti agent
# menulis ke SD card tanpa ada yang menyadarinya.
sudo mkfs.ext4 -L fleetview-data /dev/sda1
UUID=$(sudo blkid -s UUID -o value /dev/sda1)
echo "UUID=${UUID} /var/lib/fleetview ext4 defaults,noatime 0 2" | sudo tee -a /etc/fstab
sudo mkdir -p /var/lib/fleetview && sudo mount -a
findmnt /var/lib/fleetview    # wajib muncul; kalau tidak, JANGAN lanjut
```

`noatime` mengurangi tulis yang tidak perlu. Pada perangkat yang menulis
terus-menerus selama bertahun-tahun, itu berarti umur pakai.

## 3. Waktu

```bash
sudo systemctl enable --now chrony
```

Kapal sering kehilangan sumber waktu berhari-hari. Sistem sudah dirancang untuk
itu — **nomor sequence, bukan jam dinding, yang menjadi kebenaran urutan** — dan
lompatan jam ditandai (`clock_adjusted`), tidak pernah dikoreksi diam-diam.
chrony tetap dipasang supaya timestamp masuk akal saat sumber waktu tersedia.

Jangan pasang `fake-hwclock` bersamaan dengan chrony tanpa memahami interaksinya;
keduanya bisa saling menimpa dan menghasilkan lompatan jam palsu berulang.

## 4. InfluxDB lokal

```bash
curl -s https://repos.influxdata.com/influxdata-archive.key \
  | gpg --dearmor | sudo tee /usr/share/keyrings/influxdata.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/influxdata.gpg] \
https://repos.influxdata.com/debian stable main" \
  | sudo tee /etc/apt/sources.list.d/influxdata.list
sudo apt-get update && sudo apt-get install -y influxdb2

sudo systemctl enable --now influxdb
influx setup --username fleetview --org fleetview --bucket telemetry \
  --retention 90d --force
```

Retensi 90 hari, bukan tak terbatas. Disk penuh menghentikan akuisisi, dan itu
kegagalan yang paling merugikan karena terjadi diam-diam.

## 5. Pasang Edge Agent

Repo ini berisi **dua sistem yang dideploy terpisah**. Kapal hanya butuh
`shared/` dan `edge/`; `central/` — API pusat dan dashboard — tidak pernah
dipasang maupun dijalankan di sini.

```bash
sudo apt-get install -y git
sudo mkdir -p /opt/fleetview
sudo git clone --depth 1 --branch <tag rilis> <url-repo> /opt/fleetview/src
sudo /opt/fleetview/src/edge/deploy/scripts/install.sh
```

Clone penuh **aman**: `install.sh` hanya menyalin `edge/`, `shared/`, `docs/`,
dan `pyproject.toml` ke direktori rilis, lalu memasang empat paket edge saja.
Kode central ikut ter-clone tetapi tidak pernah dipasang, tidak pernah dijalankan,
dan tidak masuk `PATH`.

Kalau ingin kode central sama sekali tidak mendarat di perangkat — lebih rapi
untuk teknisi yang membuka Pi, dan `node_modules` dashboard tidak ikut tersalin:

```bash
sudo /opt/fleetview/src/edge/deploy/scripts/clone-edge.sh <url-repo> <tag rilis>
```

Skrip itu memakai sparse checkout, jadi hanya `shared/`, `edge/`, dan `docs/`
yang diambil. `docs/` sengaja ikut: kapal tidak punya internet, dan panduan
troubleshooting justru paling dibutuhkan di tempat ia tidak bisa diunduh.

**Kenapa tetap satu repo dan bukan dua.** `shared/contracts` harus identik di
kedua sisi. Kalau dipisah menjadi dua repo, format wire bisa menyimpang tanpa ada
yang menangkapnya sampai sebuah kapal berhenti bisa menyetor data.

Skrip ini idempoten: menjalankannya ulang akan memutakhirkan berkas dan merestart
layanan tanpa menyentuh data. Ia memasang rilis ke direktori bertanda versi lalu
menukar symlink `current` — itulah yang membuat rollback menjadi satu perintah.

Pada tahap ini `install.sh` akan memberi peringatan bahwa `agent.env` belum ada.
Itu wajar: identitas kapal diberikan di langkah berikutnya.

## 6. Identitas kapal dan kredensial

Di sinilah kedua potongan dari **Tahap 1** dipakai. Kalau belum punya, kembali
ke [07 §Tahap 1](07-ship-onboarding.md#tahap-1--daftarkan-kapal-di-central) —
Pi tidak bisa mendaftarkan dirinya sendiri.

```bash
sudo cp /opt/fleetview/src/edge/agent/config/edge.example.yaml /etc/fleetview/edge.yaml
sudo cp /opt/fleetview/src/edge/agent/config/sensors.example.yaml /etc/fleetview/sensors.yaml
sudo nano /etc/fleetview/edge.yaml    # isi ship_id, ship_name, device_id, central_url
```

```bash
sudo tee /etc/fleetview/agent.env >/dev/null <<'ENV'
FLEETVIEW_EDGE_CONFIG=/etc/fleetview/edge.yaml
ENV

sudo tee /etc/fleetview/secrets.env >/dev/null <<'ENV'
FLEETVIEW_STORAGE__INFLUX_TOKEN=<token influx lokal>
FLEETVIEW_SYNC__DEVICE_CLIENT_ID=<client_id dari central>
FLEETVIEW_SYNC__DEVICE_SECRET=<secret dari central>
ENV

sudo chmod 0600 /etc/fleetview/secrets.env
sudo chown root:fleetview /etc/fleetview/secrets.env
```

Rahasia hanya di `secrets.env`, tidak pernah di `edge.yaml`. `edge.yaml` akan
sering dibuka, disalin, dan dilampirkan saat diagnosis.

## 7. Verifikasi sebelum dikirim

Jalankan seluruhnya. Perangkat tidak boleh meninggalkan darat bila ada satu pun
yang gagal.

```bash
sudo systemctl restart fleetview-agent
systemctl is-active fleetview-agent                 # -> active
systemctl show fleetview-agent -p WatchdogUSec      # -> 60000000
```

```bash
# 1. Akuisisi benar-benar maju
sleep 30 && systemctl status fleetview-agent | grep Status
#    -> Status: "polls=30 pending=... record"     angka polls harus bertambah

# 2. Data benar-benar durable
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT COALESCE(SUM(record_count),0) FROM outbox;"     # -> > 0

# 3. Console bisa dibuka
curl -sf http://127.0.0.1:8080/ >/dev/null && echo "console ok"

# 4. Selamat dari mati listrik  <-- yang paling penting
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT COALESCE(SUM(record_count),0) FROM outbox;"     # catat angkanya
sudo systemctl stop fleetview-agent
# CABUT DAYA secara fisik. Tunggu 10 detik. Pasang kembali.
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT COALESCE(SUM(record_count),0) FROM outbox;"     # tidak boleh berkurang
sudo sqlite3 /var/lib/fleetview/outbox.db "PRAGMA integrity_check;"   # -> ok

# 5. Watchdog benar-benar bekerja
sudo systemctl kill -s SIGSTOP fleetview-agent
sleep 90
systemctl status fleetview-agent | head -5   # harus sudah direstart systemd
```

Uji nomor 4 dan 5 sering dilewati karena merepotkan. Justru keduanya yang
memverifikasi janji utama sistem ini. Mencabut daya di meja kerja jauh lebih
murah daripada menemukan masalahnya saat kapal sudah di laut.

## 8. Sebelum dikirim

- [ ] Label fisik: nama kapal, `ship_id`, tanggal provisioning, versi agent
- [ ] `sudo systemctl enable fleetview-agent fleetview-agent-backup.timer`
- [ ] SSH hanya dengan kunci; login kata sandi mati
- [ ] Catat `client_id` di daftar aset; **jangan** catat `secret` di mana pun
      selain di perangkat itu sendiri
- [ ] Foto pemasangan kabel serial ke LP-A104 untuk rujukan teknisi

## Catatan LP-A104

Adapter `lp_a104` **belum bisa dipakai di produksi**. Jalur baca dari perangkat
belum terkonfirmasi, dan adapter-nya sengaja gagal keras dengan pesan yang
menjelaskan — bukan diam-diam mengembalikan data kosong. Lihat
[docs/hardware/LP-A104.md](../hardware/LP-A104.md).

Sampai jalur itu dikonfirmasi bersama vendor, Pi boleh di-provisioning penuh
tetapi commissioning akuisisi belum bisa diselesaikan. **Jangan** menyiasatinya
dengan adapter `simulator`: kapal yang mengirim data simulasi akan tampak sehat
sempurna di dashboard dan tidak akan ada yang menyadarinya.
