# 04 — Prosedur Backup

Yang dicadangkan ditentukan oleh satu pertanyaan: **kalau ini hilang, apakah bisa
dibangun kembali dari tempat lain?**

| Data | Ada di mana lagi | Dicadangkan? |
|---|---|---|
| Outbox kapal yang **belum** ter-ACK | tidak ada di mana pun | **Ya, harian** |
| Outbox yang sudah ter-ACK | central | tidak |
| InfluxDB kapal | central (setelah ACK) | tidak |
| PostgreSQL central | tidak ada di mana pun | **Ya, harian** |
| InfluxDB central | sebagian di kapal, selama masa tenggang | **Ya, inkremental + snapshot mingguan** |
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

**InfluxDB** dicadangkan inkremental, hanya 48 jam terakhir. Cadangan penuh
telemetry armada berukuran terabyte dan tidak praktis harian.

### Snapshot mingguan

Yang inkremental saja tidak cukup untuk pemulihan penuh. Tambahkan di crontab
root:

```cron
0 3 * * 0 influx backup /var/backups/fleetview/weekly/$(date -u +\%Y\%m\%d) --bucket telemetry
```

Simpan snapshot mingguan **di luar server**. Cadangan yang berada di disk yang
sama dengan datanya tidak melindungi dari kegagalan yang paling mungkin terjadi:
disk itu sendiri.

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
| InfluxDB central | 24 jam (7 hari untuk data lama) | 4 jam |
| Outbox kapal | 24 jam | 30 menit |

RPO 24 jam untuk PostgreSQL bisa diperketat dengan WAL archiving bila kebutuhan
berubah. Untuk saat ini tidak dipasang: ia menambah komponen yang harus dipantau
dan dipulihkan, sementara kehilangan maksimal 24 jam metadata dapat diterima —
telemetry-nya sendiri tetap aman di kapal selama masa tenggang outbox.
