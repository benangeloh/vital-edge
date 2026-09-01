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

### Deteksi otomatis

```bash
sudo fleetview-storage
```

Mengenali tiap perangkat blok (NVMe, SSD, SSD-USB, HDD, kartu SD) dan menilai
kecocokannya. `install.sh` menjalankannya otomatis di akhir pemasangan dan
mencetak hasilnya — tidak perlu langkah terpisah untuk sekadar tahu.

Kalau ada kandidat yang lebih baik dari mount sekarang, ia menyarankan perintah
penyiapannya:

```bash
sudo fleetview-storage --siapkan /dev/sda
```

**Ini merusak** — memformat perangkat yang disebut dan memindahkan
`/var/lib/fleetview` ke sana secara permanen (lewat UUID di `/etc/fstab`, bukan
`/dev/sda1` — urutan penamaan perangkat USB bisa berubah setelah reboot). Isi
`/var/lib/fleetview` yang sudah ada disalin, bukan dihapus. Perintah ini
menolak menyentuh perangkat yang memuat sistem operasi atau sedang ter-mount,
dan meminta nama perangkatnya diketik ulang sebagai konfirmasi.

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

**Zona waktu UTC — dianjurkan, bukan wajib.**

Telemetry **tidak terpengaruh** zona waktu sistem. Timestamp dibuat sebagai epoch
mikrodetik lewat `now_micros()`, dan seluruh datetime di sistem ini sadar-timezone
(datetime naif ditolak di batas sistem). WIB, WITA, atau UTC menghasilkan angka
yang persis sama.

Yang terpengaruh adalah **tampilan log**: `journalctl` dan `systemctl status`
menampilkan waktu menurut zona sistem. Itu sebabnya UTC dianjurkan untuk armada:

- Indonesia melintasi tiga zona waktu, dan kapal berpindah di antaranya. Log dari
  kapal yang sama bisa terlihat melompat ke belakang setelah zonanya diubah.
- Menyandingkan log 70 kapal dengan zona berbeda saat menyelidiki satu kejadian
  menuntut konversi di kepala, tepat ketika ketelitian paling dibutuhkan.

Kalau seluruh armada beroperasi di satu zona dan teknisi lebih nyaman membaca WIB,
itu **pilihan yang sah**. Yang sebaiknya dihindari hanyalah mengubah zona pada
perangkat yang sudah berjalan — riwayat log-nya jadi tidak berkesinambungan.

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

## 4. Pasang Edge Agent

Repo ini berisi **dua sistem yang dideploy terpisah**, tetapi seluruhnya hanya
3,3 MB — kode central di dalamnya cuma 508 KB dan tidak pernah dipasang. Jadi
untuk perangkat baru, clone biasa sudah paling sederhana:

```bash
sudo mkdir -p /opt/fleetview
sudo git clone --depth 1 --branch <tag rilis> <url-repo> /opt/fleetview/src
sudo /opt/fleetview/src/edge/deploy/scripts/install.sh
```

Satu perintah ini mengurus semuanya: prasyarat sistem (`git`, `python3-venv`,
`sqlite3`, `gnupg`), InfluxDB lokal (dipasang **dan** di-provisioning dengan
retensi 90 hari, kata sandi acak, dan token yang langsung ditaruh di
`agent.env`), venv, keempat paket edge, unit systemd, dan config awal. Ia hanya
menyalin `edge/`, `shared/`, `docs/`, dan `pyproject.toml` ke direktori rilis —
kode central ikut ter-clone tetapi tidak pernah dipasang.

Di akhir, ia mencetak penilaian penyimpanan otomatis dan alamat halaman setup:

```
── Media penyimpanan yang terdeteksi ──────────────
  mmcblk0    119.2G   sdcard    jangan untuk data
             fsync tiap detik menghabiskannya dalam hitungan bulan

  Buka di peramban:   http://<ip-pi>:8080/setup
  PIN setup      :    ######
```

<details>
<summary>Kalau ingin kode central sama sekali tidak mendarat di perangkat</summary>

```bash
sudo /opt/fleetview/src/edge/deploy/scripts/clone-edge.sh <url-repo> <tag rilis>
```

Sparse checkout, hanya `shared/`, `edge/`, `docs/`. Penghematannya kecil —
sekitar 700 KB — jadi ini soal kerapian, bukan ukuran. Perlu diketahui: skrip
ini ada **di dalam** repo, jadi tidak bisa dipakai untuk clone pertama; ia
berguna untuk memutakhirkan perangkat yang sudah terpasang.
</details>

**Kenapa tetap satu repo dan bukan dua.** `shared/contracts` harus identik di
kedua sisi. Kalau dipisah menjadi dua repo, format wire bisa menyimpang tanpa ada
yang menangkapnya sampai sebuah kapal berhenti bisa menyetor data.

Skrip ini idempoten: menjalankannya ulang akan memutakhirkan berkas dan
merestart layanan tanpa menyentuh data maupun identitas yang sudah ada. Ia
memasang rilis ke direktori bertanda versi lalu menukar symlink `current` —
itulah yang membuat rollback menjadi satu perintah.

## 5. Penyimpanan

```bash
sudo fleetview-storage
```

Sudah tercetak otomatis di akhir langkah 4; jalankan lagi kapan saja untuk
memeriksa. Kalau ada kandidat lebih baik dari kartu SD (NVMe, SSD, SSD-USB), ia
menyarankan perintah `--siapkan`-nya — lihat
[§Kenapa SSD](#kenapa-ssd-dan-kenapa-tidak-perlu-besar) di atas untuk rincian
dan pengamannya.

**Kalau perangkat data sudah dipasang sebelum langkah 4**, urutannya tidak
masalah — `install.sh` menulis ke `${FLEETVIEW_DATA_DIR:-/var/lib/fleetview}`
mengikuti apa pun yang ter-mount di sana saat itu.

## 6. Identitas kapal dan kredensial

Diisi lewat **halaman setup di Edge Console**, bukan dengan menyunting YAML.
Kapal harus sudah didaftarkan lebih dulu di dashboard — kalau belum, lihat
[07 §Tahap 1](07-ship-onboarding.md#tahap-1--daftarkan-kapal-di-central); Pi
tidak bisa mendaftarkan dirinya sendiri.

1. Buka `http://<ip-pi>:8080/setup` dari laptop di jaringan yang sama
2. Isi **alamat pusat**, **client_id**, **secret** (dari dashboard), dan
   **PIN** (dicetak `install.sh`, atau `sudo fleetview-status` kapan saja)
3. Tekan **Sambungkan**

Nama kapal, `ship_id`, dan `device_id` diambil otomatis dari central — tidak
ada UUID yang diketik ulang. Kredensialnya diuji ke central lebih dulu; kalau
ditolak, tidak ada yang tersimpan.

Agent akan berhenti sendiri lalu systemd menyalakannya ulang dengan identitas
terisi. Console tidak punya autentikasi sendiri, jadi halaman ini dilindungi
PIN — siapa pun di jaringan kapal yang tidak memegang perangkatnya tidak bisa
mengarahkannya ke central lain.

## 7. Verifikasi sebelum dikirim

Jalankan seluruhnya. Perangkat tidak boleh meninggalkan darat bila ada satu pun
yang gagal.

```bash
sudo systemctl restart fleetview-agent
systemctl is-active fleetview-agent                 # -> active
systemctl show fleetview-agent -p WatchdogUSec      # -> 60000000
```

```bash
# 1. Ringkasan cepat — mencakup poin 1-3 di bawah sekaligus
sudo fleetview-status

# 2. Akuisisi benar-benar maju
sleep 30 && systemctl status fleetview-agent | grep Status
#    -> Status: "polls=30 pending=... record"     angka polls harus bertambah

# 3. Data benar-benar durable
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT COALESCE(SUM(record_count),0) FROM outbox;"     # -> > 0

# 4. Console bisa dibuka (mengalihkan ke /setup bila belum dikonfigurasi)
curl -sf http://127.0.0.1:8080/ >/dev/null && echo "console ok"

# 5. Selamat dari mati listrik  <-- yang paling penting
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT COALESCE(SUM(record_count),0) FROM outbox;"     # catat angkanya
sudo systemctl stop fleetview-agent
# CABUT DAYA secara fisik. Tunggu 10 detik. Pasang kembali.
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT COALESCE(SUM(record_count),0) FROM outbox;"     # tidak boleh berkurang
sudo sqlite3 /var/lib/fleetview/outbox.db "PRAGMA integrity_check;"   # -> ok

# 6. Watchdog benar-benar bekerja
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
