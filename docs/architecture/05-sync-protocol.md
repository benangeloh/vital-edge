# 05 — Desain Protokol Sync

## 1. Jaminan yang diberikan

| Sifat | Cara mencapainya |
|---|---|
| **Durable sebelum sync** | Pembacaan di-commit ke outbox SQLite (fsync) sebelum apa pun terjadi. Sync berada persis di hilir. |
| **Idempotent** | `batch_id` (UUIDv7, dibuat di edge) menjadi primary key ledger server. Kiriman ulang batch yang sudah committed akan memutar ulang ACK yang tersimpan. |
| **Aman dari duplikat (lapis ke-2)** | Identitas titik InfluxDB adalah `measurement + tags + timestamp`. Bahkan batch yang diterapkan ulang akan menimpa di tempat; tidak mungkin menggandakan. |
| **Resumable** | Chunked upload session tersimpan di sisi server. Saat tersambung lagi, edge bertanya chunk mana yang sudah masuk dan hanya mengirim sisanya. |
| **Bisa diverifikasi** | SHA-256 per chunk plus SHA-256 seluruh payload, keduanya dicek sebelum commit. |
| **Terurut & tanpa celah** | `sequence` monoton per kapal, diberikan di dalam transaksi yang sama dengan insert outbox. Celah menjadi mustahil secara struktural. |
| **Tidak hilang saat sync gagal** | Baris outbox meninggalkan status `pending` hanya setelah ACK. Retensi hanya memangkas baris yang sudah acked. |

**Semantik pengiriman: at-least-once di jaringan, effectively-exactly-once di storage.**
Kita tidak mengejar exactly-once dalam pengiriman — itu memang tidak bisa dicapai di
koneksi yang tidak andal — melainkan membuat pengiriman berulang jadi tidak berbahaya,
baik di level ledger maupun di level storage.

## 2. Batch envelope

```jsonc
{
  "schema_version": "1.0",
  "batch_id":       "018f2c...",        // UUIDv7 — terurut waktu, dibuat di edge
  "ship_id":        "uuid",
  "device_id":      "uuid",
  "sequence_start": 1045001,
  "sequence_end":   1045500,
  "first_timestamp":"2026-08-31T04:00:00.000Z",
  "last_timestamp": "2026-08-31T04:08:19.000Z",
  "record_count":   500,
  "payload_checksum":"sha256:ab12...",   // atas byte payload TERKOMPRESI
  "payload_encoding":"zstd+msgpack",
  "payload_size":   184320,
  "agent_version":  "1.0.0",
  "config_version": "cfg-2026-08-01-3",
  "created_at":     "2026-08-31T04:08:20.000Z"
}
```

Payload = `zstd(msgpack([Reading, ...]))`, dengan tiap `Reading`:

```jsonc
{"seq":1045001,"ts":1756612800000000,"sensor_id":"me_port_rpm",
 "measurement":"rpm","value":742.5,"unit":"rpm","quality":"good",
 "tags":{"engine":"port"}}
```

`quality` ∈ `good | stale | suspect | substituted`. Pembacaan yang buruk **tetap dikirim
disertai flag quality, bukan dibuang** — dengan begitu sistem pusat bisa membedakan
"sensornya rusak" dari "kita memang tidak punya datanya", dan itu penting untuk analitik
perawatan.

## 3. Alur normal

```
edge                                              central
 │ GET  /ingest/sync-state ─────────────────────►
 │ ◄──── {last_contiguous_sequence: 1045000,
 │        committed_batch_ids:[...]}
 │  (lewati apa pun yang sudah committed)
 │
 │ POST /ingest/sessions  {header} ─────────────►  baris ledger status=staging
 │ ◄──── 201 {session_id, chunk_size, received_chunks: []}
 │
 │ PUT  /sessions/{sid}/chunks/0 ───────────────►  verifikasi sha chunk, simpan
 │ PUT  /sessions/{sid}/chunks/1 ───────────────►
 │ ...
 │ POST /sessions/{sid}/commit {checksum} ──────►  rakit ulang → verifikasi sha payload
 │                                                 → verifikasi record_count & rentang seq
 │                                                 → tx PG: ledger jadi committed
 │                                                          + majukan watermark
 │                                                 → tulis ke Influx
 │ ◄──── 200 ACK {batch_id, "committed",
 │        last_contiguous_sequence: 1045500,
 │        server_received_at}
 │
 │ baris outbox 1045001..1045500 → state=acked
```

## 4. Melanjutkan setelah terputus

```
 │ POST /ingest/sessions {batch_id yang sama} ──►
 │ ◄──── 200 {session_id, received_chunks: [0,1,2]}
 │ PUT  chunk 3..N saja ────────────────────────►
 │ POST commit ─────────────────────────────────►  ACK
```

Kalau ternyata batch itu sudah sepenuhnya committed sebelum link terputus, `POST /sessions`
yang sama mengembalikan `{status:"already_committed", ack}` dan edge langsung menandainya
acked tanpa mengirim satu byte pun. **ACK yang hilang hanya berbiaya satu round trip,
tidak pernah upload ulang.**

## 5. Watermark dan penanganan celah

- Server menyimpan `last_contiguous_sequence` (yang resmi) dan `highest_sequence_seen`.
- `highest > contiguous` ⇒ ada celah (normal terjadi setelah import USB berisi data lama
  masuk lebih dulu daripada batch online yang sedang dalam perjalanan).
- Celah ditampilkan di halaman detail kapal, bukan didiamkan diam-diam.
- Edge mengirim **yang paling lama dulu**, agar watermark contiguous maju dengan stabil dan
  angka "pending" di dashboard punya arti.

## 6. Export USB / HDD

```
FLEETVIEW_EXPORT/
  manifest.json          # export_id, ship_id, device_id, created_at, agent_version,
                         # daftar batch beserta checksum, export_checksum, signature
  batches/
    018f2c...-1045001-1045500.fvb    # identik byte-per-byte dengan payload HTTP
```

- Export bersifat **non-destruktif**: batch tetap `pending` di kapal sampai ACK sungguhan
  datang. Jadi flashdisk yang hilang atau rusak tidak menghilangkan apa pun.
- `tools/usb-import` memverifikasi manifest, lalu mem-POST tiap batch ke endpoint
  `/api/v1/ingest` yang *sama*. Tidak ada implementasi ingest kedua yang perlu dijaga
  agar tetap sinkron.
- ACK pulang-pergi: proses import menghasilkan `ack-receipt.json` yang bisa dibawa kembali
  oleh teknisi dan diterapkan di kapal (`POST /api/sync/apply-receipt`), sehingga baris-baris
  tadi akhirnya dilepas. Kalau receipt-nya tidak pernah kembali, sync online berikutnya akan
  meng-ACK-nya secara idempotent — datanya memang sudah tersimpan di pusat, jadi pengulangan
  itu tidak berefek apa-apa.

## 7. Penanganan waktu

Jam kapal bisa melenceng dan bisa melompat mundur. Karena itu:

- **`sequence` yang menjadi patokan urutan. Jam dinding tidak pernah.**
- Setiap titik data membawa `ts` (jam device, µs). Central tambahan mencatat
  `server_received_at`.
- Heartbeat mengembalikan `server_time`; edge menghitung dan melaporkan skew-nya. Skew yang
  melewati ambang memunculkan health event dan ditampilkan di halaman detail kapal.
- Kalau jam melompat mundur di tengah jalan, pembacaan tetap punya sequence monoton dan
  diberi flag `clock_adjusted`. Tidak ada yang dibuang dan tidak ada yang diam-diam dikoreksi.

## 8. Pemilihan transport & backoff

```
rank(link) = terjangkau(central) ∧ masih_dalam_budget ∧ skor_kestabilan
urutan:  LAN  >  Wi-Fi  >  Seluler
```

- Reachability diuji dengan probe `/health` sungguhan, bukan sekadar "interface up" — Wi-Fi
  kapal yang berhasil associate tapi tidak punya route adalah kasus yang paling sering terjadi.
- Seluler punya budget byte bulanan yang bisa dikonfigurasi; kalau habis, ia turun ke mode
  heartbeat + alert saja sampai periode berikutnya.
- Retry: exponential backoff dengan jitter, 5 detik → maksimum 30 menit, dengan penghitung
  percobaan per batch.
- Setelah N kegagalan keras berturut-turut, batch dikarantina dan dilaporkan — operator jadi
  bisa melihatnya, bukan hanya menyaksikan antrean membesar tanpa penjelasan.
- Saat tersambung lagi setelah offline lama, pengosongan antrean dilakukan **dengan
  pembatasan laju**, yang paling lama dulu, dan menghormati `429`.

## 9. Catatan implementasi Phase 4

### Yang berubah dari desain awal

**Encoding payload: `gzip+json`, bukan `zstd+msgpack`.** gzip ada di stdlib —
tidak menambah dependency pada Pi yang berjalan 24/7 — dan JSON bisa dibaca
manusia. Yang kedua itu berharga saat teknisi membuka berkas export USB di kapal
untuk mencari tahu kenapa sesuatu tidak sampai. Telemetry JSON ter-gzip menyusut
sekitar 85%, cukup dekat dengan zstd untuk data sebesar ini.

**Masa tenggang dihitung dari waktu ACK, bukan waktu pengukuran.** Ini koreksi
terhadap desain awal yang keliru. Kapal yang offline 30 hari lalu menyetor
seluruh backlog-nya sekaligus akan punya data berumur 30 hari yang baru saja
di-ACK; kalau tenggang dihitung dari waktu pengukuran, semuanya terhapus seketika
dan jendela replay lokal hilang tepat saat paling dibutuhkan.

### Celah sequence

Nomor sequence dialokasikan dan di-commit sebelum record disimpan. Kalau proses
mati di antara keduanya, nomor itu hilang dan meninggalkan celah.

Celah **tidak** diperlakukan sebagai kehilangan data — ia menjadi **batas batch**.
Batcher hanya mengambil baris yang sequence-nya berurutan, sehingga invariant
`record_count == sequence_end - sequence_start + 1` selalu terpenuhi, dan batch
berikutnya mulai setelah celah.

### Prioritas pengiriman

| Prioritas | Isi | Perilaku di link bermeteran |
|---|---|---|
| 0 CRITICAL | Alarm, health event, perubahan status | Selalu dikirim, bahkan saat kuota habis |
| 1 SUMMARY | Agregat, data ter-downsample | Dikirim sampai kuota habis |
| 2 RAW | Telemetry mentah | Ditahan lebih dulu saat bandwidth sempit |

Semua baris dalam satu batch berbagi prioritas yang sama, sehingga penyaringan
berdasarkan prioritas benar-benar berlaku per batch. Kuota seluler yang habis
**tidak** mematikan link — ia menurunkannya ke CRITICAL saja.

### USB: transport, bukan database

Berkas di `telemetry/` **byte-nya identik** dengan yang akan dikirim lewat HTTP.
Alat impor di kantor mem-POST berkas itu ke endpoint `/api/v1/ingest` yang sama
persis. Hanya ada satu jalur ingest yang perlu diuji dan diamankan — dan jalur
USB, yang paling jarang dipakai, tidak menjadi yang paling jarang teruji.

Export **tidak pernah menandai batch synchronized**: `FileExportTransport`
mengembalikan `ack=None`, dan Sync Engine hanya menandai acked bila ACK sungguhan
diterima. Flashdisk yang hilang atau rusak karena itu tidak menghilangkan apa pun.
