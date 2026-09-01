# 01 — Deployment Produksi

Dokumen ini menjelaskan bentuk sistem saat berjalan di produksi: apa yang
dideploy ke mana, siapa menjalankan apa, dan bagaimana keduanya bertahan tanpa
pengawasan.

Prosedur langkah-per-langkah ada di dokumen terpisah:

| Kebutuhan | Dokumen |
|---|---|
| Menyiapkan Raspberry Pi baru | [02 — Provisioning Raspberry Pi](02-provisioning-raspberry-pi.md) |
| Memasang / memutakhirkan central | [03 — Deployment Central](03-central-deployment.md) |
| Pencadangan | [04 — Prosedur Backup](04-backup.md) |
| Pemulihan bencana | [05 — Disaster Recovery](05-disaster-recovery.md) |
| Mengganti perangkat rusak | [06 — Penggantian Perangkat](06-device-replacement.md) |
| Menambah kapal baru | [07 — Onboarding Kapal](07-ship-onboarding.md) |
| Menonaktifkan kapal | [08 — Decommissioning Kapal](08-ship-decommissioning.md) |
| Ada yang tidak beres | [09 — Troubleshooting](09-troubleshooting.md) |

---

## 1. Dua sistem, bukan satu

```
┌─ DI KAPAL (×70+) ───────────────┐        ┌─ DI DARAT (×1) ─────────────────┐
│                                 │        │                                 │
│  fleetview-agent.service        │        │  nginx (TLS, rate limit)        │
│    ├── collector  (LP-A104)     │        │    └── fleetview-central.service│
│    ├── outbox     (SQLite)      │ ─────► │          ├── ingest             │
│    ├── sync engine              │  batch │          ├── control            │
│    └── console    (:8080)       │        │          └── query              │
│                                 │        │                                 │
│  influxdb  (lokal, 90 hari)     │        │  postgresql   (relasional)      │
│                                 │        │  influxdb     (telemetry armada)│
└─────────────────────────────────┘        └─────────────────────────────────┘
```

Satu build Edge Agent yang identik dipasang ke seluruh kapal. Yang membedakan
mereka hanya `/etc/fleetview/edge.yaml` dan kredensial perangkatnya. Konsekuensi
praktisnya: rilis diuji sekali, lalu dipasang di mana saja tanpa varian per
kapal — dan bug yang muncul di satu kapal bisa direproduksi di kapal mana pun.

## 2. Yang membuatnya bertahan tanpa pengawasan

Raspberry Pi di kapal tidak punya orang yang mengawasinya, dan kunjungan teknisi
bisa berjarak berbulan-bulan. Tiga lapis pertahanan:

**`Restart=always` tanpa batas percobaan.** Default systemd menyerah setelah 5
restart dalam 10 detik dan meninggalkan unit mati. Di kapal itu berarti akuisisi
berhenti sampai ada yang naik ke kapal, jadi `StartLimitIntervalSec=0`.

**Watchdog (`WatchdogSec=60`).** Menangkap kegagalan yang `Restart=always` tidak
bisa lihat: proses yang **masih hidup tetapi berhenti bekerja**, misalnya loop
akuisisi tersangkut pada pembacaan serial yang tidak pernah kembali. Agent harus
membuktikan `polls_attempted` benar-benar bertambah; kalau tidak, ping berhenti
dan systemd merestart.

Sengaja dipakai `polls_attempted`, bukan `polls_succeeded`: perangkat mati atau
kabel lepas membuat polling gagal terus-menerus, dan itu **tidak** bisa
diperbaiki dengan restart. Merestart berulang dalam keadaan itu hanya membuang
siklus akuisisi dan menyulitkan diagnosis.

**Write barrier.** Sekali `OutboxSink.append()` kembali, data sudah fsync ke
SQLite dan selamat dari mati listrik. InfluxDB dan sync berada di hilir dan boleh
gagal. Karena itu unit agent memakai `Wants=influxdb.service`, bukan `Requires`:
InfluxDB yang mati tidak boleh menghentikan akuisisi.

## 3. Layout di kapal

```
/opt/fleetview/
  releases/<versi>/     salinan lengkap tiap rilis, termasuk venv-nya
  current -> releases/<versi>     symlink; rollback = tukar symlink
  venv    -> current/venv
/etc/fleetview/
  edge.yaml             identitas kapal + tuning. Boleh dibaca saat diagnosis.
  sensors.yaml          pemetaan sensor
  agent.env             environment non-rahasia
  secrets.env           token. mode 0600, TIDAK ikut dicadangkan.
/var/lib/fleetview/
  outbox.db             data yang belum ter-ACK. Ini yang tidak tergantikan.
  influx/               telemetry lokal, retensi 90 hari
  backups/              7 cadangan harian terakhir
```

Rahasia dipisah dari konfigurasi supaya `edge.yaml` bisa dibaca, disalin, dan
dilampirkan saat diagnosis tanpa ikut membocorkan token.

## 4. Kapasitas

Angka terukur (Phase 9 dan 10, 100 sensor pada 1 Hz per kapal):

| Besaran | Nilai |
|---|---|
| Outbox terpakai | 25 byte/titik |
| Outbox per kapal per hari | 0,22 GB |
| **Outbox untuk 30 hari offline** | **6,5 GB** |
| InfluxDB lokal, terpakai di disk | 25,8 byte/titik → **0,22 GB/hari** |
| InfluxDB lokal, retensi 90 hari | **20 GB** |
| Bandwidth per kapal per hari | 62 MB terkompresi |
| Bandwidth armada berkelanjutan | 0,40 Mbit/dtk untuk 70 kapal |
| Puncak memori agent | ~167 MB |
| CPU per kapal | ~1,4% satu inti |

Outbox menyimpan payload ter-gzip per chunk. Sebelum Phase 10 ia menyimpan JSON
polos pada 352 byte/titik — 79 GB untuk 30 hari offline, lebih dari separuh SSD
128 GB yang dianjurkan. Detailnya di
[`outbox/store.py`](../../edge/agent/src/fleetview_edge/outbox/store.py).

Dijumlahkan, satu kapal butuh sekitar **37 GB**:

| Bagian | Ruang |
|---|---|
| InfluxDB lokal, retensi 90 hari | 20 GB |
| Outbox, skenario offline 30 hari | 6,5 GB |
| OS + aplikasi | ~10 GB |
| **Total** | **~37 GB** |

Artinya **128 GB sudah sangat lapang** — tidak perlu 500 GB. Yang menentukan
bukan kapasitasnya, melainkan jenis medianya; lihat
[02 — Kebutuhan](02-provisioning-raspberry-pi.md#kebutuhan).

**SSD 128 GB adalah minimum, bukan anjuran.** SD card boleh untuk boot, tetapi
`/var/lib/fleetview` harus di SSD: siklus tulis outbox pada 1 Hz akan menghabiskan
SD card dalam hitungan bulan.

## 5. Versi konfigurasi

`config_version` di `edge.yaml` ikut dikirim dalam setiap batch envelope, bersama
`agent_version`. Ini bukan hiasan: saat satu kapal berperilaku aneh, pertanyaan
pertama selalu "versi apa yang menghasilkan ini", dan tanpa jawabannya diagnosis
lintas 70 kapal menjadi tebakan.

Setiap perubahan `edge.yaml` **wajib** menaikkan `config_version`. Gunakan format
`cfg-YYYY-MM-DD-N`.

## 6. Strategi pembaruan

Pemutakhiran dilakukan bertahap, tidak pernah ke seluruh armada sekaligus:

1. **Satu kapal percontohan** — kapal yang punya konektivitas paling baik supaya
   masalah terlihat cepat. Amati 72 jam.
2. **Sepuluh kapal** dengan campuran transport (LAN pelabuhan, 4G, satelit).
   Amati satu minggu.
3. **Sisanya**, maksimal 10 kapal per hari.

Yang diamati di setiap tahap, lewat dashboard central: jumlah `pending` tidak
menumpuk, tidak ada batch masuk karantina, dan `polls_failed` tidak naik.

Pemutakhiran hanya dilakukan saat kapal terjangkau. Kapal yang sedang di laut
dengan backlog besar **tidak** dimutakhirkan — biarkan ia menyetor backlog-nya
lebih dulu.

## 7. Rollback

```bash
sudo /opt/fleetview/deploy/scripts/rollback.sh          # rilis sebelumnya
sudo /opt/fleetview/deploy/scripts/rollback.sh v0.9.2   # rilis tertentu
```

Rollback hanya menukar symlink. Data tidak disentuh: outbox dan InfluxDB tetap di
tempatnya dan nomor sequence tetap berlanjut, sehingga central tidak melihat apa
pun yang aneh selain jeda singkat.

**Satu pengecualian penting:** rilis yang menaikkan versi skema outbox tidak bisa
di-rollback begitu saja — rilis lama tidak mengenali skema baru. Lihat
[05 — Disaster Recovery](05-disaster-recovery.md).
