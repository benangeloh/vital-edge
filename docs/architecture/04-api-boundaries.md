# 04 — Batas API

Ada tiga bidang (plane) yang sengaja dipisahkan. Ketiganya berbeda dalam hal siapa yang
melakukan autentikasi, apa yang boleh disentuh, dan bagaimana perilakunya saat gagal.

| Bidang | Base path | Pemanggil | Autentikasi | Profil beban |
|---|---|---|---|---|
| **Ingest** | `/api/v1/ingest/*` | Edge agent | JWT device, terbatas satu `ship_id` | Volume tinggi, meledak-ledak, kena backpressure |
| **Control** | `/api/v1/{fleet,ships,config,alerts,admin}/*` | Dashboard, operator | JWT user + RBAC | Volume rendah |
| **Query** | `/api/v1/telemetry/*` | Dashboard | JWT user + RBAC | Sedang, hanya baca |
| **Provisioning** | `/api/v1/provisioning/*` | Device baru, admin | Enrollment token / JWT admin | Jarang |

Pemisahan ini penting secara operasional: badai reconnect seluruh armada di bidang ingest
tidak boleh sampai membuat dashboard tidak bisa dipakai. Tiap bidang punya rate limit,
connection pool, dan SLO sendiri.

## 1. Bidang Ingest (edge → central)

```
POST   /api/v1/ingest/sessions                  buka/lanjutkan upload untuk satu batch
       body: header batch (lihat 05 §2)
       201 {session_id, chunk_size, received_chunks: []}
       200 {status: "already_committed", ack}          ← pengulangan idempotent

GET    /api/v1/ingest/sessions/{session_id}     info resume: chunk mana yang sudah masuk
PUT    /api/v1/ingest/sessions/{sid}/chunks/{i} byte mentah; header X-Chunk-SHA256
POST   /api/v1/ingest/sessions/{sid}/commit     body {payload_checksum} → ACK
DELETE /api/v1/ingest/sessions/{session_id}     batalkan staging

POST   /api/v1/ingest/batches                   sekali kirim untuk batch kecil (< 1 MB)

GET    /api/v1/ingest/sync-state                apa yang sudah dimiliki server untuk kapal ini
       200 {last_contiguous_sequence, highest_sequence_seen,
            committed_batch_ids: [...], server_time}

POST   /api/v1/ingest/heartbeat                 tanda hidup + health + estimasi pending
       200 {server_time, config_version, commands: []}
```

`heartbeat` sekaligus berfungsi sebagai jalur downlink: ia mengembalikan `server_time`
(untuk deteksi clock skew) dan versi config yang menunggu, sehingga edge tidak perlu
koneksi masuk dan tidak perlu loop polling tersendiri.

## 2. Bidang Control (dashboard → central)

```
GET  /api/v1/fleet/overview        grid 70 kapal: status, lag sync, pending, alert
                                   PostgreSQL saja — InfluxDB tidak ikut di jalur ini
GET  /api/v1/fleet/events          SSE: perubahan status + alert
GET  /api/v1/ships?q=&status=&sync_status=&sort=
GET  /api/v1/ships/{id}            detail: identitas, device, sensor, health, versi config
GET  /api/v1/ships/{id}/sync       riwayat batch, watermark, celah, riwayat transport
GET  /api/v1/ships/{id}/sensors
PUT  /api/v1/ships/{id}/config     membuat config_version baru (tidak pernah menimpa di tempat)
GET  /api/v1/alerts?severity=&ack=
POST /api/v1/alerts/{id}/acknowledge
GET  /api/v1/admin/{users,roles,agent-versions,audit}
```

## 3. Bidang Query

```
GET /api/v1/telemetry/series
    ?ship_id=&sensor_id=&from=&to=&interval=auto|1m|1h&agg=mean|max|min|last
    → {series: [{sensor_id, unit, points: [[ts, value], ...]}], bucket_used, downsampled}

GET /api/v1/telemetry/latest?ship_id=       nilai terakhir yang diketahui per sensor
GET /api/v1/telemetry/track?ship_id=&from=&to=   jalur GPS, disederhanakan di sisi server
```

`interval=auto` memilih bucket berdasarkan lebar rentang waktu, dan response menyebutkan
bucket mana yang menjawab — sehingga chart bisa jujur memberi label "rata-rata per jam"
alih-alih terkesan menampilkan data mentah.

## 4. API Edge Console (hanya localhost / LAN kapal)

Terikat ke interface lokal, satu kredensial operator, tidak pernah diekspos ke internet.

```
GET  /api/status            sistem, link LPA, collector, InfluxDB, storage, network, sync
GET  /api/sensors           per sensor: nilai terakhir, umur data, quality, penghitung error
GET  /api/sync              batch pending, umur unacked tertua, sukses terakhir, error terakhir
POST /api/sync/trigger      paksa percobaan sync sekarang
GET  /api/export/targets    mount point USB/HDD yang terdeteksi
POST /api/export/start      mulai export USB     → export_id
GET  /api/export/{id}       progres / hasil verifikasi
GET  /api/logs?level=&since=&component=
GET  /api/config            config efektif (secret disamarkan)
PUT  /api/config            hanya override lokal; key yang dikelola pusat akan ditolak
```

## 5. Konvensi (berlaku di semua bidang)

- **Envelope:** `{ok, data, error: {code, message, details}, meta}` — satu bentuk di mana-mana.
- **Versioning:** major di URL (`/api/v1`); payload batch membawa `schema_version` sendiri
  agar edge agent versi lama tetap bisa upload selama masa rollout.
- **Idempotensi:** setiap penulisan ingest dikunci oleh `batch_id`. Pengulangan itu gratis
  dan aman.
- **Error:** `4xx` = jangan diulang (perbaiki request-nya). `5xx` / `429` = ulangi dengan
  backoff. Edge menerapkan pembedaan ini secara harfiah, sehingga batch yang rusak permanen
  dikarantina dan dilaporkan, bukan diulang selamanya.
- **Backpressure:** `429` disertai `Retry-After` adalah response kelas satu yang memang
  diharapkan saat reconnect seluruh armada — bukan kondisi error.
- **OpenAPI** digenerate dari aplikasi FastAPI; tipe TS dashboard digenerate darinya di CI.
