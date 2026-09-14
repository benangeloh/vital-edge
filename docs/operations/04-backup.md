# 04 — Prosedur Backup

Yang dicadangkan ditentukan oleh satu pertanyaan: **kalau ini hilang, apakah bisa
dibangun kembali dari tempat lain?**

| Data | Ada di mana lagi | Dicadangkan? |
|---|---|---|
| Outbox kapal yang **belum** ter-ACK | tidak ada di mana pun | **Ya, harian** |
| Outbox yang sudah ter-ACK | central | tidak |
| InfluxDB kapal | central (setelah ACK) | tidak |
| PostgreSQL central | tidak ada di mana pun | **Ya, harian** |
| InfluxDB central | sebagian di kapal, selama masa tenggang | **Ya, penuh setiap hari** |
| Konfigurasi kapal | repo + inventaris | **Ya, harian** |
| Rahasia | penerbit kredensial | tidak — sengaja |

Rahasia sengaja **tidak** dicadangkan. Kredensial perangkat diterbitkan ulang saat
provisioning, jadi menyalinnya ke direktori cadangan hanya memperbanyak tempat
token bisa bocor tanpa mempermudah pemulihan.

---

## Di kapal

Berjalan otomatis lewat `fleetview-agent-backup.timer` (harian).

Timer memakai `Persistent=true`: Pi kapal sering mati listrik, dan tanpa itu
pencadangan yang terlewat akan dilewati diam-diam alih-alih dijalankan saat
perangkat menyala kembali.

```bash
sudo systemctl start fleetview-agent-backup.service    # manual
sudo systemctl list-timers fleetview-agent-backup.timer
ls -lh /var/lib/fleetview/backups/
```

Yang perlu diperhatikan pada skrip [`backup-edge.sh`](../../edge/deploy/scripts/backup-edge.sh):

- Memakai `sqlite3 .backup`, **bukan** `cp`. Menyalin berkas SQLite yang sedang
  ditulis menghasilkan berkas rusak atau setengah jadi.
- Menjalankan `PRAGMA integrity_check` **saat mencadangkan**, bukan saat memulihkan.
  Cadangan rusak yang tidak diketahui lebih buruk daripada tidak punya cadangan
  sama sekali, karena ia menciptakan rasa aman yang keliru.
- Memangkas cadangan lama **setelah** cadangan baru terverifikasi, sehingga
  kegagalan tidak pernah meninggalkan kapal tanpa cadangan sama sekali.

Retensi 7 hari. Cadangan tetap berada di kapal — mengunggahnya akan bersaing
dengan sinkronisasi telemetry di jalur yang sama sempitnya, untuk data yang
justru sedang dalam perjalanan ke central.

## Di central

Berjalan otomatis lewat `fleetview-backup.timer` (harian 02:30 UTC).

```bash
sudo systemctl start fleetview-backup.service     # manual
ls -lh /var/backups/fleetview/
```

**PostgreSQL** dicadangkan penuh dengan `pg_dump -Fc`. Format custom dipilih
karena bisa dipulihkan sebagian — saat pemulihan darurat, memulihkan hanya tabel
ledger jauh lebih cepat daripada seluruh basis data. Hasilnya diverifikasi dengan
`pg_restore --list`, karena `pg_dump` bisa sukses menghasilkan berkas yang tidak
bisa dibaca kembali bila disk penuh di tengah jalan.

Ukurannya kecil (ratusan MB) tetapi isinya paling tidak tergantikan: identitas
kapal, kredensial perangkat, dan **ledger batch**. Ledger itu yang membuat
pengiriman ulang tidak menggandakan data — kehilangannya tidak menghilangkan
telemetry, tetapi membuat kapal yang mengirim ulang backlog menghasilkan duplikat.

**InfluxDB** dicadangkan **penuh setiap hari**, bukan inkremental. `influx
backup` di InfluxDB 2.x (CLI yang dibawa image `influxdb:2.7-alpine`) tidak
punya flag rentang waktu (`--start`/`--end`) sama sekali — dikonfirmasi lewat
`influx backup --help` di container dan dokumentasi resmi InfluxDB v2. Filter
rentang waktu hanya ada di perintah lain yang sama sekali berbeda,
`influxd inspect export-lp` (ekspor line-protocol mentah, hasilnya **tidak
bisa** dipulihkan lewat `influx restore`), jadi bukan pengganti yang valid
untuk pipeline backup/restore ini.

**Implikasi biaya operasional — baca ini sebelum kaget lihat grafik disk:**
setiap cadangan harian sekarang menyalin **seluruh bucket yang masih
tersimpan**, bukan cuma 48 jam terakhir seperti desain semula. Dengan retensi
`DOCKER_INFLUXDB_INIT_RETENTION` 365 hari (lihat
[`docker-compose.prod.yml`](../../docker-compose.prod.yml)) dan skala armada
yang didokumentasikan di
[03-central-deployment.md](03-central-deployment.md) (~10 GB/hari tersimpan
pasca-kompresi, ~3,6 TB pada hari ke-365), cadangan InfluxDB **hari ke-365**
bisa berukuran mendekati 3,6 TB — dan setiap harinya, seluruh bucket sebesar
itu disalin ulang dari nol, bukan delta. Ini jauh lebih besar dari beban
disk/waktu/bandwidth yang diasumsikan rancangan awal ("48 jam terakhir"), dan
akan terus membesar seiring bucket terisi menuju batas retensi. Pantau
`/var/backups/fleetview/` dan `FLEETVIEW_BACKUP_KEEP_DAYS` (retensi lokal
cadangan, default 30 hari) dengan asumsi ini, dan pertimbangkan menurunkan
`FLEETVIEW_BACKUP_KEEP_DAYS` atau menaikkan kapasitas disk backup jika ukuran
bucket bertumbuh signifikan. Mekanisme inkremental yang sesungguhnya (mis.
`influxd inspect export-lp` dengan jalur restore terpisah) adalah desain yang
lebih invasif dan di luar cakupan naskah ini — dicatat sebagai kandidat kerja
lanjutan, bukan diimplementasikan di sini.

### Replikasi offsite

Karena cadangan harian sekarang sudah **penuh**, tidak perlu lagi cron
terpisah yang menjalankan `influx backup` mingguan hanya demi mendapatkan
salinan penuh — cadangan harian sudah memberikannya. Yang masih dibutuhkan
adalah menyalin hasilnya **ke luar server**: cadangan yang berada di disk
yang sama dengan datanya tidak melindungi dari kegagalan yang paling mungkin
terjadi, yaitu disk itu sendiri. Jadwalkan replikasi berkala (mis. mingguan)
dari seluruh direktori cadangan:

```bash
rsync -a --delete /var/backups/fleetview/ backup-host:/srv/fleetview-backups/
```

---

## Uji pemulihan

**Cadangan yang belum pernah dipulihkan bukan cadangan — ia baru asumsi.**

Jadwalkan uji pemulihan setiap kuartal ke server terpisah, dan catat waktunya.
Angka itulah RTO sebenarnya; perkiraan di atas kertas selalu terlalu optimistis.

```bash
# Postgres ke basis data sementara
createdb fleetview_uji
pg_restore -d fleetview_uji /var/backups/fleetview/<stamp>/postgres.dump
psql -d fleetview_uji -c "SELECT COUNT(*) FROM ships;"
psql -d fleetview_uji -c "SELECT COUNT(*) FROM sync_batches;"

# Outbox kapal
sqlite3 /path/ke/outbox.db "PRAGMA integrity_check;"
sqlite3 /path/ke/outbox.db \
  "SELECT state, SUM(record_count) FROM outbox GROUP BY state;"
```

Yang diverifikasi bukan sekadar "perintahnya berhasil", melainkan jumlah kapal,
jumlah baris ledger, dan jumlah record outbox masuk akal dibanding produksi.

## Ringkasan sasaran

| | RPO | RTO |
|---|---|---|
| PostgreSQL central | 24 jam | 1 jam |
| InfluxDB central | 24 jam | 4 jam |
| Outbox kapal | 24 jam | 30 menit |

RPO 24 jam untuk PostgreSQL bisa diperketat dengan WAL archiving bila kebutuhan
berubah. Untuk saat ini tidak dipasang: ia menambah komponen yang harus dipantau
dan dipulihkan, sementara kehilangan maksimal 24 jam metadata dapat diterima —
telemetry-nya sendiri tetap aman di kapal selama masa tenggang outbox.
