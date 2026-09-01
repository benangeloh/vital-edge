# 02 — Alur Data

## 1. Jalur akuisisi (berjalan tanpa peduli kondisi jaringan)

```
Sensor / PLC
   │  (fieldbus — lihat docs/hardware/LP-A104.md)
   ▼
Autonics LP-A104            HMI + PLC logic (BUKAN gateway)
   │  ▲ JALUR BACA BELUM TERKONFIRMASI — 6 kandidat, lihat LP-A104.md
   ▼
┌──────────────────────── Raspberry Pi — Edge Agent ────────────────────────┐
│                                                                           │
│  ProtocolAdapter.read()   →  Parser  →  Validator                         │
│   (mock | simulator |         frame      range, laju perubahan,           │
│    lp_a104 [TODO])           mentah      staleness → flag quality         │
│                                              │                            │
│                                              ▼                            │
│                                   ╔══════════════════════╗                │
│                                   ║  WRITE BARRIER       ║                │
│                                   ║  satu tx SQLite:     ║                │
│                                   ║   seq = ++counter    ║                │
│                                   ║   INSERT baris outbox║                │
│                                   ║   COMMIT (fsync)     ║  ← jaminan     │
│                                   ╚══════════════════════╝     durabilitas│
│                                              │                            │
│                        ┌─────────────────────┴──────────────────┐         │
│                        ▼                                        ▼         │
│              Worker ingest Influx                        Sync Engine      │
│              (async, di-retry)                           (async)          │
│                        │                                        │         │
│                        ▼                                        │         │
│                 InfluxDB lokal                                  │         │
│                 (query, chart,                                  │         │
│                  retensi lokal)                                 │         │
│                        │                                        │         │
│                        └──────────► Edge Console ◄──────────────┘         │
└───────────────────────────────────────────────────────────────────────────┘
```

**Write barrier adalah inti dari jaminan offline-first.** Sebuah pembacaan baru diakui
"berhasil dikumpulkan" setelah transaksi SQLite-nya commit. Sejak detik itu, data selamat
dari mati listrik, dari InfluxDB yang sedang mati, dan dari kondisi jaringan apa pun.
Ingest ke InfluxDB dan sinkronisasi keduanya berada persis di hilir dan keduanya bisa diulang.

Inilah alasan InfluxDB tidak berada di jalur kritis akuisisi: kalau InfluxDB lokal crash atau
sedang compaction, akuisisi tetap berjalan tanpa terganggu dan worker ingest akan menyusul.

## 2. Jalur offload

```
Sync Engine
   │
   ├─ Batcher: kumpulkan baris outbox belum-ACK yang berurutan menjadi satu batch
   │           (dibatasi oleh jumlah, ukuran byte, atau umur maksimum)
   │
   ├─ Network Manager: probe & urutkan link
   │      LAN ──► Wi-Fi ──► 4G/5G ──► tidak ada
   │      (reachability + budget kuota + kestabilan, bukan sekadar "interface up")
   │
   ▼
Transport                       satu Sync Engine, dua transport
   ├─ HttpTransport ──── TLS ───────────────────────► Central /api/v1/ingest
   └─ FileTransport ──► folder export USB/HDD
                                │
                                │  dibawa secara fisik
                                ▼
                        tools/usb-import  ──────────► Central /api/v1/ingest
                                                       (endpoint sama persis,
                                                        validasi sama persis)
```

USB adalah **transport**, bukan database. Byte yang ditulis ke flashdisk identik dengan apa
yang akan dikirim `HttpTransport`, sehingga hanya ada satu jalur ingest yang perlu diuji dan
satu yang perlu diamankan.

## 3. Jalur di central

```
POST /api/v1/ingest/...
   │
   ├─ autentikasi device  (JWT per-device, terbatas pada satu ship_id)
   ├─ cek idempotensi     (batch_id sudah committed? → putar ulang ACK tersimpan, berhenti)
   ├─ perakitan chunk     (resumable; ditampung di disk sampai lengkap)
   ├─ verifikasi payload_checksum + record_count + schema_version
   │
   ├─ tx PostgreSQL: insert baris ledger batch, majukan watermark kapal, audit
   └─ tulis InfluxDB: titik data ber-tag ship_id/device_id/sensor_id
   │        (identitas titik = measurement+tags+timestamp ⇒ pengiriman ulang menimpa, tidak menggandakan)
   ▼
ACK { batch_id, status, last_contiguous_sequence, server_received_at }
   │
   ▼
Edge menandai baris outbox sebagai acked. Baris baru boleh dipangkas setelah ACK + masa tenggang.
```

## 4. Jalur baca (dashboard)

```
central/web ──► GET /api/v1/fleet/overview        ← PostgreSQL saja (cepat, cache 5 dtk)
            ──► GET /api/v1/ships/{id}            ← PostgreSQL
            ──► GET /api/v1/telemetry/series      ← InfluxDB (sadar downsampling)
            ──► SSE /api/v1/fleet/events          ← push status/alert
```

Render daftar armada tidak pernah menyentuh InfluxDB. Status kapal, lag sync, dan jumlah
pending semuanya sudah didenormalisasi ke PostgreSQL oleh jalur ingest, sehingga tampilan
70 kapal hanya butuh satu query ber-index berapa pun besar volume telemetry-nya.

## 5. Perilaku saat gagal

| Kegagalan | Perilaku |
|---|---|
| LP-A104 tidak terjangkau | Collector menandai sensor `stale`, backoff, memunculkan health event. Tidak ada data palsu yang ditulis. |
| InfluxDB lokal mati | Akuisisi dan sync tetap jalan. Worker ingest me-retry; chart di Console menurun kualitasnya, panel status tidak terpengaruh. |
| Outbox SQLite penuh / disk menipis | Pangkas baris yang sudah acked lebih dulu, lalu perpendek retensi InfluxDB lokal. **Jangan pernah hapus baris yang belum acked.** Alert saat watermark kritis. |
| Jaringan putus di tengah upload | Upload session tetap tersimpan di server; edge melanjutkan dari `received_chunks` pada percobaan berikutnya. |
| ACK hilang padahal server sudah commit | Edge mengulang; server memutar ulang ACK yang tersimpan (idempotent). Tidak ada penyimpanan ganda. |
| Central mati | Edge menumpuk data; `pending_batches` bertambah; Console menampilkan backlog beserta estimasinya. |
| Edge mati listrik di tengah transaksi | WAL SQLite membatalkan transaksi parsial. Counter sequence dan outbox tetap konsisten. |
