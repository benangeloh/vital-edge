# 09 — Troubleshooting

Diurutkan berdasarkan seberapa sering masalahnya benar-benar terjadi.

**Aturan pertama:** kalau ragu, jangan hentikan agent. Selama agent berjalan,
data terus terkumpul dan tersimpan. Hampir semua diagnosis bisa dilakukan tanpa
menghentikannya, dan agent yang dihentikan "sebentar" untuk diperiksa adalah
penyebab kehilangan data yang paling sering terjadi.

---

## Diagnosis pertama, apa pun gejalanya

```bash
systemctl status fleetview-agent          # baris Status: memuat angka operasional
journalctl -u fleetview-agent -n 100 --no-pager
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT state, SUM(record_count) FROM outbox GROUP BY state;"
df -h /var/lib/fleetview
```

Empat perintah ini menjawab: apakah agent hidup, apa yang terakhir dikatakannya,
apakah data terkumpul, dan apakah masih ada ruang.

Log berbentuk JSON terstruktur. Untuk membacanya:

```bash
journalctl -u fleetview-agent -o cat | jq -r \
  '"\(.timestamp) \(.level) \(.event)"'
journalctl -u fleetview-agent -o cat | jq 'select(.level=="error")'
```

---

## Kapal tampak offline di dashboard

Bedakan lebih dulu, karena penanganannya berlawanan:

| Gejala | Artinya |
|---|---|
| Data lama masih masuk, `pending` menurun | Kapal sedang menyusul backlog. **Biarkan.** |
| Tidak ada data masuk sama sekali | Masalah konektivitas atau autentikasi |
| Data masuk tetapi `pending` naik terus | Produksi melebihi kemampuan kirim |

```bash
curl -s https://fleetview.example.com/api/v1/sync/ships/<ship_id> \
  -H "Authorization: Bearer <token>" | jq
```

Kapal yang offline berminggu-minggu adalah keadaan **normal** dan sistem
dirancang untuk itu. Yang perlu dikhawatirkan adalah kapal yang seharusnya
terjangkau tetapi tidak mengirim apa pun.

## `pending` terus bertambah dan tidak pernah turun

```bash
journalctl -u fleetview-agent -o cat | jq 'select(.event | startswith("sync."))' | tail -30
```

Kemungkinan penyebab, urut dari yang paling sering:

**Tidak ada konektivitas.** Wajar di laut. Periksa `sync.transport_unavailable`.
Tidak ada yang perlu dilakukan.

**Autentikasi gagal.** Cari `401` atau `auth`. Biasanya kredensial dicabut atau
salah disalin. Perbaiki `secrets.env` lalu restart.

**Batch masuk karantina.** Batch yang gagal permanen berhenti dicoba ulang supaya
tidak menyumbat antrean, tetapi **datanya tidak dihapus**:

```bash
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT batch_id, attempts, last_error FROM batches WHERE state='failed';"
```

Setelah penyebabnya diperbaiki, lepaskan kembali ke antrean:

```bash
sudo systemctl stop fleetview-agent
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "UPDATE outbox SET state='pending', batch_id=NULL
   WHERE batch_id IN (SELECT batch_id FROM batches WHERE state='failed');
   DELETE FROM batches WHERE state='failed';"
sudo systemctl start fleetview-agent
```

**Anggaran seluler habis.** Sistem menurunkan diri ke prioritas CRITICAL saja,
tidak mati sepenuhnya. Log memuat `sync.budget_exhausted`. Ini perilaku yang
disengaja, bukan kegagalan.

**Data ditolak central.** Cari `422`. Penyebab paling umum: titik lebih tua
daripada retensi bucket InfluxDB di central. Perpanjang retensi
([05 Skenario B](05-disaster-recovery.md#skenario-b--disk-central-penuh)) sebelum
data itu ditolak permanen.

## Agent gagal start berulang

```bash
journalctl -u fleetview-agent -n 50 --no-pager | grep -i "config error\|Traceback"
```

**`config error [config.*]`** — konfigurasi tidak lengkap. Pesannya menyebutkan
apa yang kurang. Agent sengaja gagal cepat alih-alih berjalan setengah benar.

**`database disk image is malformed`** — outbox rusak. Lihat
[05 Skenario D](05-disaster-recovery.md#skenario-d--outbox-rusak).

**Gagal tanpa pesan, `Type=notify` timeout** — agent tidak sempat mengirim
`READY=1`. Hampir selalu berarti ia tersangkut saat menyiapkan komponen. Periksa
apakah InfluxDB lokal, port serial, dan direktori data bisa diakses.

## Agent hidup tetapi tidak ada data terkumpul

```bash
systemctl status fleetview-agent | grep Status    # angka polls harus bertambah
journalctl -u fleetview-agent -o cat | jq 'select(.event | startswith("collector."))' | tail -20
```

**`polls` tidak bertambah sama sekali** — loop akuisisi tersangkut. Watchdog
seharusnya sudah merestart dalam 60 detik; kalau tidak, periksa `WatchdogSec`
benar-benar aktif:

```bash
systemctl show fleetview-agent -p WatchdogUSec    # -> 60000000
```

**`polls` bertambah tetapi `polls_failed` ikut naik** — perangkat lapangan tidak
menjawab. Ini **bukan** sesuatu yang bisa diperbaiki dengan restart, dan itu
sebabnya watchdog memakai `polls_attempted`, bukan `polls_succeeded`. Periksa
kabel serial, daya perangkat, dan parameter port.

**Adapter `lp_a104` gagal keras dengan pesan penjelasan** — ini disengaja. Jalur
baca dari LP-A104 belum terkonfirmasi; lihat
[docs/hardware/LP-A104.md](../hardware/LP-A104.md). Adapter menolak menebak
alih-alih diam-diam mengembalikan data kosong.

## Disk kapal penuh

```bash
df -h /var/lib/fleetview
sudo du -sh /var/lib/fleetview/*
```

Urutan tindakan:

1. **Periksa apakah `/var/lib/fleetview` benar-benar di SSD.** Kalau `findmnt`
   tidak menunjukkan mount terpisah, agent sedang menulis ke SD card — itu
   penyebabnya, dan SD card-nya sudah dalam bahaya.
2. **Pangkas InfluxDB lokal**, bukan outbox:
   ```bash
   influx bucket update --name telemetry --retention 30d
   ```
3. **Jangan pernah menghapus baris outbox yang `pending`.** Itu satu-satunya
   salinan data yang belum pernah sampai ke mana pun. Yang boleh dipangkas hanya
   yang sudah `acked`, dan itu sudah dilakukan otomatis setelah masa tenggang.

## Console tidak bisa dibuka

```bash
curl -sf http://127.0.0.1:8080/ >/dev/null && echo ok
grep -A3 "^console:" /etc/fleetview/edge.yaml
```

Default `host: 127.0.0.1` berarti Console hanya bisa diakses dari Pi itu sendiri.
Untuk akses dari LAN kapal, ubah ke `0.0.0.0` — sadari bahwa Console **tidak
punya autentikasi**, jadi lakukan hanya bila jaringan kapal memang tertutup.

Console dirancang tetap berfungsi tanpa JavaScript dan mengisolasi kegagalan per
bagian: satu panel yang error tidak membuat seluruh halaman kosong.

## Central lambat atau menolak permintaan

```bash
curl -sf https://fleetview.example.com/api/v1/health -H "Authorization: Bearer <token>" | jq
sudo journalctl -u fleetview-central -n 100 --no-pager
df -h /var/lib/influxdb
```

**Banyak 429** — pembatasan laju nginx. Wajar saat banyak kapal menyetor backlog
bersamaan, dan kapal akan mencoba ulang sendiri. Kalau berlanjut, naikkan `rate`
zona `ingest` di `/etc/nginx/sites-available/fleetview` lalu `nginx -s reload` —
tidak perlu deploy ulang aplikasi.

**Banyak 5xx** — periksa PostgreSQL dan InfluxDB lewat `/api/v1/health`.

**Disk hampir penuh** — lihat
[05 Skenario B](05-disaster-recovery.md#skenario-b--disk-central-penuh). Ini
satu-satunya kegagalan yang menghentikan ingest untuk seluruh armada sekaligus.

## Timestamp tampak salah di dashboard

Periksa zona waktu Pi lebih dulu:

```bash
timedatectl    # harus UTC
```

Zona waktu sistem **tidak** menggeser telemetry — timestamp dibuat sebagai epoch
mikrodetik dan tidak bergantung padanya. Kalau angka di dashboard tampak bergeser,
sebabnya ada di tempat lain: jam perangkat yang salah, atau lompatan jam.

Yang dipengaruhi zona waktu hanyalah tampilan `journalctl` dan `systemctl status`.

Kalau zona waktunya benar, cari penanda lompatan jam:

```bash
journalctl -u fleetview-agent -o cat | jq 'select(.event=="clock.jump_detected")'
```

Lompatan jam **ditandai, tidak pernah dikoreksi diam-diam**, dan record yang
terpengaruh membawa tag `clock_adjusted`. Urutan datanya tetap benar karena
**nomor sequence, bukan jam dinding, yang menjadi kebenaran urutan**.

## Yang harus dikumpulkan sebelum meminta bantuan

```bash
journalctl -u fleetview-agent --since "24 hours ago" -o cat > /tmp/agent.log
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT state, SUM(record_count) FROM outbox GROUP BY state;" > /tmp/outbox.txt
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT batch_id, state, attempts, last_error FROM batches
   ORDER BY sequence_start DESC LIMIT 20;" >> /tmp/outbox.txt
systemctl status fleetview-agent > /tmp/status.txt
cp /etc/fleetview/edge.yaml /tmp/       # aman: rahasia ada di secrets.env
df -h > /tmp/disk.txt
```

`edge.yaml` aman dilampirkan — rahasia berada di `secrets.env` yang terpisah.
**Jangan pernah melampirkan `secrets.env`.**

Sertakan juga `ship_id`, versi agent, `config_version`, dan kapan gejalanya mulai.
