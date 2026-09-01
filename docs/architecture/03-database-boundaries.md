# 03 — Batas Antar-Database

Ada empat store. Masing-masing memegang tepat satu jenis kebenaran.

| Store | Lokasi | Menyimpan | Tidak boleh menyimpan |
|---|---|---|---|
| **SQLite** | Edge | Journal sync: counter sequence, outbox (payload batch + state-nya), cache config, log health/event | Apa pun yang di-query sebagai telemetry |
| **InfluxDB (edge)** | Edge | Time-series telemetry lokal untuk chart Console + retensi lokal | Config, sync state, kredensial |
| **PostgreSQL** | Central | Seluruh kebenaran relational/metadata | **Nilai telemetry** |
| **InfluxDB (central)** | Central | Time-series telemetry seluruh armada | Data relational, config, secret |

> **Tentang SQLite di edge:** spesifikasi melarang PostgreSQL sebagai *primary telemetry
> storage* di edge, dan itu dipatuhi — InfluxDB tetap menjadi telemetry store. SQLite hanya
> menyimpan pembukuan sync dan payload batch terkompresi yang menunggu ACK. Ia adalah
> journal, bukan database telemetry: tidak ada satu pun kode yang membaca nilai sensor darinya.

## 1. Edge — sketsa schema SQLite

```sql
counters(name TEXT PK, value INTEGER NOT NULL);          -- 'sequence' — monoton, per kapal

outbox(
  sequence        INTEGER PRIMARY KEY,                   -- tanpa celah, diberikan di dalam tx
  captured_at     INTEGER NOT NULL,                      -- epoch µs, jam device
  payload         BLOB    NOT NULL,                      -- zstd(msgpack([Reading,...]))
  record_count    INTEGER NOT NULL,
  state           TEXT    NOT NULL,                      -- pending|batched|in_flight|acked
  batch_id        TEXT    NULL REFERENCES batches(batch_id),
  influx_written  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_outbox_state ON outbox(state, sequence);

batches(
  batch_id        TEXT PRIMARY KEY,                      -- UUIDv7, dibuat di edge
  sequence_start  INTEGER NOT NULL,
  sequence_end    INTEGER NOT NULL,
  first_timestamp INTEGER NOT NULL,
  last_timestamp  INTEGER NOT NULL,
  record_count    INTEGER NOT NULL,
  payload_checksum TEXT   NOT NULL,                      -- sha256, hex
  schema_version  TEXT    NOT NULL,
  state           TEXT    NOT NULL,                      -- built|uploading|acked|failed
  transport       TEXT    NULL,                          -- lan|wifi|cellular|usb
  attempts        INTEGER NOT NULL DEFAULT 0,
  acked_at        INTEGER NULL,
  last_error      TEXT    NULL
);

health_events(id INTEGER PK, ts INTEGER, severity TEXT, component TEXT, code TEXT, detail TEXT);
config_cache(key TEXT PK, value TEXT, config_version TEXT, applied_at INTEGER);
```

Mode WAL, dengan `synchronous=FULL` pada jalur tulis outbox. Retensi: baris yang sudah acked
dipangkas setelah masa tenggang yang bisa dikonfigurasi (default 7 hari) — ini menyisakan
jendela replay lokal seandainya central pernah kehilangan data.

## 2. Schema InfluxDB (identik di edge dan central)

Satu measurement untuk satu besaran fisik — dengan begitu pertumbuhan schema mengikuti
*jenis* sensor, bukan *jumlah unit* sensor, sehingga sensor baru tidak butuh perubahan schema.

| Measurement | Field | Catatan |
|---|---|---|
| `rpm` | `value` (float) | |
| `fuel_level` | `value` | |
| `fuel_flow` | `value` | |
| `pressure` | `value` | tag `point` membedakan lube-oil / fuel / air |
| `temperature` | `value` | tag `point`: exhaust / coolant / ambient |
| `voltage` | `value` | |
| `engine_load` | `value` | |
| `position` | `lat`, `lon`, `sog`, `cog` | satu titik ⇒ lat/lon selalu konsisten |
| `_unknown` | `value` | sensor yang belum terdaftar masuk ke sini; tidak pernah dibuang |

**Tag (sama di mana-mana):**
`ship_id`, `device_id`, `sensor_id`, `engine` (`port`/`stbd`/`aux1`/…), `unit`, `quality`.

Tag dirancang berkardinalitas rendah. `sensor_id` adalah slug stabil dari registry sensor,
**bukan** label bebas — jadi ledakan kardinalitas yang biasa terjadi di InfluxDB dicegah di
level contract, bukan sekadar lewat kesepakatan tim.

**Bucket:**

| Bucket | Retensi | Ditulis oleh |
|---|---|---|
| edge `telemetry` | 90 hari (dijaga watermark disk) | worker ingest edge |
| central `telemetry` | 400 hari | modul ingest |
| central `telemetry_1m` | 3 tahun | task downsampling |
| central `telemetry_1h` | selamanya | task downsampling |

Query rentang waktu di dashboard memilih bucket berdasarkan lebar jendela waktunya, sehingga
chart 1 tahun membaca `telemetry_1h` dan tetap cepat.

## 3. PostgreSQL central — kelompok tabel

| Kelompok | Tabel |
|---|---|
| Identity | `users`, `roles`, `user_roles`, `sessions` |
| Fleet | `ships`, `devices`, `sensors`, `sensor_types`, `voyages` |
| Provisioning | `device_credentials`, `enrollment_tokens` |
| Config | `ship_configs`, `ship_config_versions`, `config_rollouts` |
| Sync | `sync_batches` (ledger), `upload_sessions`, `upload_chunks`, `ship_sync_state` |
| Ops | `agent_versions`, `alert_rules`, `alert_events`, `audit_logs` |

Ada dua tabel yang menopang tampilan armada dan perlu disorot:

```sql
ship_sync_state(                        -- tepat satu baris per kapal; sumber data fleet-overview
  ship_id UUID PK REFERENCES ships,
  last_contiguous_sequence BIGINT NOT NULL DEFAULT 0,   -- watermark resmi milik server
  highest_sequence_seen    BIGINT NOT NULL DEFAULT 0,   -- > contiguous ⇒ masih ada celah
  last_batch_received_at   TIMESTAMPTZ,
  last_telemetry_timestamp TIMESTAMPTZ,
  pending_estimate         BIGINT,                      -- dilaporkan oleh heartbeat edge
  connection_state         TEXT,                        -- online|degraded|offline
  active_transport         TEXT,
  edge_health              JSONB,
  config_version           TEXT,
  agent_version            TEXT
);

sync_batches(                           -- ledger untuk idempotensi + audit
  batch_id UUID PRIMARY KEY,            -- dibuat di edge ⇒ kiriman ulang jadi konflik PK, bukan duplikat
  ship_id UUID NOT NULL, device_id UUID NOT NULL,
  sequence_start BIGINT, sequence_end BIGINT,
  first_timestamp TIMESTAMPTZ, last_timestamp TIMESTAMPTZ,
  record_count INT, payload_checksum TEXT, schema_version TEXT,
  transport TEXT, status TEXT,          -- staging|committed|rejected
  received_at TIMESTAMPTZ, committed_at TIMESTAMPTZ,
  ack_payload JSONB                     -- disimpan agar ACK yang hilang bisa diputar ulang identik
);
CREATE UNIQUE INDEX ux_batch_range ON sync_batches(ship_id, sequence_start, sequence_end);
```

## 4. Aturan lintas-store

1. **Tidak ada join antar-store.** Baris InfluxDB membawa `ship_id`/`sensor_id` sebagai tag;
   lapisan API yang menempelkan nama-namanya dari PostgreSQL.
2. **PostgreSQL adalah otoritas untuk identitas.** Tag di InfluxDB adalah salinan denormalisasi.
3. **Ingest menulis ke PostgreSQL dulu, InfluxDB kemudian.** Kalau penulisan InfluxDB gagal,
   batch tetap berstatus `staging`, ACK tidak diterbitkan, dan edge mengulang — jadi central
   tidak pernah mengklaim memiliki data yang sebenarnya tidak ada padanya.
4. **Sensor yang belum terdaftar tetap disimpan, bukan dibuang** (`_unknown`), dan otomatis
   didaftarkan ke tabel `sensors` dengan `status='unregistered'` agar operator bisa memberinya
   nama nanti. Inilah yang membuat janji "sensor bisa bertambah di masa depan" jadi nyata
   tanpa perlu deploy ulang.

## 5. Catatan implementasi Phase 3

Penyimpanan InfluxDB lokal sudah dibangun; **outbox SQLite belum**, dan itu
disengaja.

Brief Phase 3 meminta agar SQLite tidak ditambahkan kecuali assessment
menunjukkan kebutuhan nyata. Assessment memang menunjukkannya — tetapi untuk
**outbox sinkronisasi** (sequence monoton, state batch yang bisa berubah), yang
merupakan lingkup Phase 4. Untuk kebutuhan Phase 3 sendiri — menyimpan telemetry
ke InfluxDB dan bertahan dari kegagalan sementara — SQLite tidak dibutuhkan.

Yang dipakai sekarang adalah **buffer percobaan ulang in-memory berbatas**:

| Ditangani | Tidak ditangani |
|---|---|
| InfluxDB restart | Mati listrik — isi buffer hilang |
| Compaction | InfluxDB mati berjam-jam — buffer penuh, record tertua dibuang |
| Lonjakan beban sesaat | |

Konsekuensinya jujur dan terlihat: `dropped_records` pada storage health adalah
**penanda kehilangan data yang nyata, dan harus selalu nol**. Kalau tidak nol,
ada data yang benar-benar hilang.

Write barrier di [`02-data-flow.md`](02-data-flow.md) belum berlaku sampai
Phase 4. Sampai saat itu, satu-satunya jaminan durabilitas adalah InfluxDB itu
sendiri setelah penulisan berhasil.

### Yang sudah ditegakkan di kode

- **Timestamp pengukuran, bukan waktu tulis.** Data yang tertahan di buffer
  mendarat di posisi waktu yang benar, bukan menggumpal di detik saat sambungan
  pulih.
- **Tag identitas**: `ship_id`, `device_id`, `sensor_id`, ditambah `quality`,
  `source`, dan `unit`. Ketiganya yang terakhir dipakai untuk memfilter query.
- **Penjagaan kardinalitas** (`storage/cardinality.py`): kunci tag yang unik
  per pembacaan — `timestamp`, `seq`, `batch_id`, dan sejenisnya — ditolak di
  kode, bukan diserahkan pada kedisiplinan saat menulis config. Anggaran seri
  untuk 70 kapal x 80 sensor adalah ~89.600, jauh di bawah batas praktis
  InfluxDB.
- **4xx vs 5xx**: `StorageRejectedError` tidak diulang, `StorageUnavailableError`
  diulang. Aturannya sama dengan protokol sync. Salah memetakan berarti buffer
  tersumbat selamanya oleh satu batch rusak.
