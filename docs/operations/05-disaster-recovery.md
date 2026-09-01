# 05 — Disaster Recovery

Urutan pemulihan yang berlaku di semua skenario:

1. **Hentikan kerusakan meluas.** Biarkan kapal mengumpulkan data. Hampir selalu
   ini berarti **jangan sentuh kapalnya** — mereka dirancang untuk bertahan
   berminggu-minggu tanpa central.
2. **Pulihkan kemampuan menerima**, bukan seluruh sejarah. Kapal akan menyetor
   ulang begitu ada yang mendengarkan.
3. **Pulihkan sejarah** dari cadangan.
4. **Verifikasi tidak ada yang ganda dan tidak ada yang hilang.**

Langkah 2 sebelum langkah 3 adalah yang penting. Selama central tidak bisa
menerima, seluruh armada terus mengisi outbox-nya; makin lama, makin besar risiko
ada kapal yang mencapai batas disk.

---

## Skenario A — Central mati total

**Yang terjadi di armada:** tidak ada yang hilang. Kapal terus mengumpulkan dan
menyimpan; sync gagal dan dicoba ulang dengan backoff. Batas amannya adalah masa
tenggang outbox, sekitar 30 hari pada kapasitas terukur.

```bash
# 1. Server baru, sampai langkah 6 di dokumen 03.
# 2. Pulihkan PostgreSQL — ledger batch yang paling penting.
sudo -u postgres createdb fleetview --owner fleetview
sudo -u fleetview pg_restore -d fleetview /var/backups/fleetview/<stamp>/postgres.dump

# 3. Samakan revisi skema dengan versi kode yang dipasang.
cd /opt/fleetview/current/central/api
sudo -u fleetview /opt/fleetview/venv/bin/alembic upgrade head

# 4. Nyalakan. Mulai menerima SEKARANG; telemetry lama menyusul.
sudo systemctl start fleetview-central
curl -sf https://fleetview.example.com/api/v1/health -H "Authorization: Bearer <token>" | jq

# 5. Baru pulihkan telemetry.
influx restore /var/backups/fleetview/weekly/<stamp> --full
```

**Ledger batch harus dipulihkan sebelum menerima ingest.** Ledger itu yang
membuat pengiriman ulang menghasilkan konflik primary key alih-alih data ganda.
Menerima ingest dengan ledger kosong berarti setiap kapal yang mengirim ulang
backlog-nya akan menciptakan duplikat.

Kalau ledger benar-benar hilang dan tidak bisa dipulihkan, duplikasi masih tertahan
oleh lapisan kedua: identitas titik InfluxDB (measurement + tag + timestamp)
membuat penulisan ulang menimpa di tempat, bukan menambah. Jumlah batch di
laporan akan tampak ganda, tetapi telemetry-nya tidak.

## Skenario B — Disk central penuh

Ini kegagalan yang paling mungkin dan paling merugikan: ia menghentikan ingest
untuk **seluruh armada sekaligus**. `fleetview-diskwatch.timer` memberi peringatan
pada 75% dan kritis pada 90% supaya ini tidak pernah menjadi kejutan.

```bash
df -h /var/lib/influxdb
sudo systemctl status fleetview-diskwatch

# Lega segera: buang cadangan lama (bukan datanya)
sudo find /var/backups/fleetview -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +

# Lalu perbaiki penyebabnya: retensi bucket
influx bucket list
influx bucket update --name telemetry --retention 180d
```

**Jangan** menurunkan retensi di bawah skenario offline terburuk yang ingin
didukung. InfluxDB menolak titik yang lebih tua dari retensi dengan HTTP 422, dan
kapal yang kembali setelah offline panjang akan kehilangan datanya secara permanen.

## Skenario C — Raspberry Pi kapal rusak

Lihat [06 — Penggantian Perangkat](06-device-replacement.md). Yang menentukan
adalah apakah SSD masih terbaca; kalau ya, seluruh outbox bisa diselamatkan.

## Skenario D — Outbox rusak

Gejalanya: agent gagal start berulang, atau log memuat `database disk image is
malformed`.

```bash
sudo systemctl stop fleetview-agent
cp /var/lib/fleetview/outbox.db /var/lib/fleetview/outbox.db.rusak
sqlite3 /var/lib/fleetview/outbox.db "PRAGMA integrity_check;"
```

```bash
# Coba selamatkan. .recover menyelamatkan lebih banyak daripada .dump pada
# berkas yang rusak, karena ia membaca halaman langsung.
sqlite3 /var/lib/fleetview/outbox.db ".recover" | sqlite3 /var/lib/fleetview/outbox.baru.db
sqlite3 /var/lib/fleetview/outbox.baru.db \
  "SELECT state, SUM(record_count) FROM outbox GROUP BY state;"
```

Bandingkan jumlahnya dengan cadangan harian terakhir, lalu pakai yang lebih
lengkap. **Simpan berkas rusaknya** sampai pemulihan terverifikasi.

Kalau tidak ada yang bisa diselamatkan, pulihkan dari cadangan dan terima
kehilangan sampai 24 jam. Sebelum menjalankan agent kembali, periksa counter
sequence-nya:

```bash
sqlite3 /var/lib/fleetview/outbox.db "SELECT value FROM counters WHERE name='sequence';"
```

Nomor sequence **tidak boleh mundur**. Kalau cadangan yang dipulihkan punya
counter lebih rendah dari yang sudah pernah dikirim, kapal akan memakai ulang
nomor yang sudah dipakai dan menimpa data lama di central. Naikkan manual di atas
`highest_sequence_seen` — bukan `last_contiguous_sequence`, karena nomor di dalam
celah pun sudah pernah terpakai:

```bash
curl -s https://fleetview.example.com/api/v1/sync/ships/<ship_id> \
  -H "Authorization: Bearer <token>" | jq .data.highest_sequence_seen
```

```bash
sqlite3 /var/lib/fleetview/outbox.db \
  "UPDATE counters SET value = <highest_sequence_seen + 1000> WHERE name='sequence';"
```

## Skenario E — Rollback melewati migrasi

Rollback kode itu aman. Rollback melewati migrasi skema tidak: rilis lama tidak
mengenali skema baru.

```bash
cd /opt/fleetview/current/central/api
sudo -u fleetview /opt/fleetview/venv/bin/alembic current
sudo -u fleetview /opt/fleetview/venv/bin/alembic history | head -20
```

Kalau revisi saat ini lebih baru daripada yang dikenal rilis tujuan:

```bash
sudo systemctl stop fleetview-central
sudo -u fleetview /opt/fleetview/venv/bin/alembic downgrade <revisi rilis lama>
sudo /opt/fleetview/deploy/scripts/rollback.sh <versi>
```

**Downgrade Alembic bisa menghilangkan data** bila migrasi yang dibatalkan
menambahkan kolom atau tabel. Selalu cadangkan lebih dulu, dan baca isi berkas
migrasinya sebelum menjalankan `downgrade`.

Untuk sisi kapal, hal setara berlaku pada skema outbox. Migrasi v1→v2 (kompresi
chunk) menulis ulang tabel outbox; rilis lama tidak bisa membacanya. Rollback
melewati batas itu memerlukan pemulihan `outbox.db` dari cadangan harian —
dengan pemeriksaan counter sequence seperti pada Skenario D.

## Verifikasi setelah pemulihan apa pun

```bash
# Tidak ada kapal yang tertinggal jauh
curl -s https://fleetview.example.com/api/v1/ships -H "Authorization: Bearer <token>" \
  | jq '[.data[] | select(.pending_records > 100000)]'

# Ledger konsisten: tidak ada batch_id ganda
psql -U fleetview -d fleetview -c \
  "SELECT batch_id, COUNT(*) FROM sync_batches GROUP BY batch_id HAVING COUNT(*) > 1;"

# Tidak ada celah sequence per kapal: kedua kolom harus sama.
# highest_sequence_seen > last_contiguous_sequence berarti ada batch yang belum
# sampai di tengah rentang.
psql -U fleetview -d fleetview -c \
  "SELECT ship_id, last_contiguous_sequence, highest_sequence_seen
   FROM ship_sync_state ORDER BY ship_id;"
```

Terakhir, buka dashboard dan periksa apakah setiap kapal yang seharusnya aktif
memang menunjukkan data yang masuk. Angka di basis data bisa terlihat benar
sementara sesuatu di jalur baca tetap salah.
