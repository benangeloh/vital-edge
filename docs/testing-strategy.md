# Strategi Pengujian

Status per Phase 9. Setiap angka di dokumen ini berasal dari menjalankan kode,
bukan dari perhitungan di atas kertas — reproduksi dengan `make bench`.

## Kategori dan cakupannya

| # | Kategori | Di mana | Jumlah | Catatan |
|---|---|---|---|---|
| 1 | Unit | `*/tests/test_*.py` | ~480 | Contracts, common, collector, storage, sync, console |
| 2 | Integrasi | `central/api/tests`, `-m integration` | 44 | Postgres + InfluxDB **sungguhan**, bukan mock |
| 3 | Protokol sync | `edge/agent/tests/test_sync_*.py` | 56 | Batching, resume, idempotensi, karantina |
| 4 | Database | `test_outbox.py` + integrasi | 23 + | Outbox SQLite; Postgres lewat integrasi |
| 5 | API | `central/api/tests/test_api_integration.py` | 25 | Auth, RBAC, ingest, envelope, OpenAPI |
| 6 | Kegagalan edge | Phase 2/3 | 40+ | LP-A104 putus, timeout, sensor rusak, InfluxDB mati |
| 7 | Kegagalan jaringan | Phase 4/8 | 30+ | Putus, 429, 5xx, resume, backoff |
| 8 | Duplikat | Phase 4/5/8 | 12 | Dua lapis: ledger PK + identitas titik InfluxDB |
| 9 | Resume | Phase 4/8 | 8 | Chunked upload, batch_id stabil, ACK diputar ulang |
| 10 | Beban | `tests/load`, `-m load` | 10 | Anggaran per kapal dan kapasitas armada |
| 11 | Keamanan | `tests/security`, `-m security` | 23 | Token, RBAC, redaksi, injeksi, kebocoran error |
| 12 | UI smoke | `central/web` + Console | 53 + 59 | Komponen, a11y (axe), kontras WCAG, halaman Console |

Total: **613 test Python + 53 TypeScript**.

## Menjalankan

```bash
make check              # lint, tipe, unit, schema drift, frontend
make test-integration   # butuh Postgres + InfluxDB
make test-e2e           # simulasi 3 kapal, 12 skenario
make test-load          # uji beban skala armada
make test-security      # pemeriksaan keamanan
make bench              # benchmark 70 kapal x 100 sensor
```

## Benchmark: 70 kapal × 100 sensor @ 1 Hz

Diukur pada mesin pengembangan (Apple Silicon). Kolom terakhir menjelaskan
seberapa jauh angkanya bisa dipindahkan ke Raspberry Pi.

| Ukuran | Nilai | Berlaku di Pi? |
|---|---|---|
| Titik per detik (armada) | **7.000** | ya — aritmetika |
| Titik per hari (armada) | **604,8 juta** | ya |
| Laju pembangkitan | 430.000 titik/dtk | tidak — Pi jauh lebih lambat |
| CPU per kapal | ~1,4% satu inti | perkiraan kasar; Pi mungkin 10–20× |
| Line protocol | 211 byte/titik | ya — hanya bentuk data |
| Volume InfluxDB mentah (armada) | 127 GB/hari | ya |
| Payload sync terkompresi | 7,2 byte/titik | ya |
| Rasio kompresi | 26,8× | ya |
| Bandwidth per kapal | 0,06 GB/hari | ya |
| Bandwidth armada | 4,4 GB/hari (0,4 Mbit/dtk) | ya |
| Baris outbox | 25 byte/titik | ya |
| Latensi fsync | 0,04 ms (macOS) | **tidak** — lihat di bawah |
| Puncak memori proses | 168 MB | indikatif |

### Yang bisa disimpulkan

**Bandwidth bukan masalah.** Seluruh armada butuh 0,4 Mbit/dtk rata-rata. Bahkan
satu link 4G sederhana per kapal cukup dengan margin sangat lebar.

**Penyimpanan InfluxDB muat.** 127 GB/hari mentah untuk armada = 1,8 GB/hari per
kapal. Dengan kompresi TSM konservatif 5×, retensi 90 hari ≈ 33 GB per kapal —
muat di SSD 128 GB yang dianjurkan assessment, bersama sistem operasi dan outbox.

**CPU kemungkinan besar cukup, tetapi belum terbukti di perangkat aslinya.**
Angka 1,4% per kapal berasal dari mesin pengembangan yang jauh lebih cepat. Ini
harus diukur ulang di Raspberry Pi sungguhan sebelum commissioning.

### Yang TIDAK bisa disimpulkan dari benchmark ini

**Laju tulis outbox.** Diukur 229.000 titik/dtk, tetapi latensi fsync di macOS
hanya 0,04 ms — `fsync()` di APFS tidak benar-benar memaksa data ke media
(butuh `F_FULLFSYNC`). SD card Raspberry Pi lazimnya 10–50 ms per fsync, yaitu
**250–1.250× lebih lambat**. Pada 1 Hz dengan satu commit per putaran, itu masih
muat — 50 ms fsync per detik = 5% waktu — tetapi marginnya jauh lebih tipis
daripada yang disiratkan angka mentahnya. Ini salah satu alasan assessment
mensyaratkan SSD, bukan SD card.

## Temuan kapasitas: outbox pada offline panjang

Diukur `tests/load/test_fleet_load.py::TestVolumeOutbox`, terhadap berkas SQLite
yang benar-benar ditulis — bukan hitungan di atas kertas.

```
Outbox terpakai       : 25,0 byte/titik  (skema lama tanpa kompresi: 352,3)
Per kapal per hari    : 0,22 GB/hari
30 hari offline       : 6,5 GB dari SSD 128 GB
```

### Bagaimana angka ini turun 12×

Phase 9 menemukan masalah nyata: outbox menyimpan JSON polos pada 352 byte/titik,
sehingga 30 hari offline — durasi terburuk yang ditetapkan assessment — memakan
**79 GB**, lebih dari separuh SSD 128 GB yang dianjurkan, sebelum menghitung
InfluxDB dan sistem operasi.

Perbaikan pertama yang dicoba, gzip per pembacaan, **hampir tidak menolong**:
352 → 220 byte/titik, hanya 1,6×. Sebabnya, satu pembacaan JSON hanya sekitar 350
byte sementara gzip membutuhkan jendela jauh lebih besar untuk menemukan
pengulangan. Redundansi telemetry ternyata ada **antar** pembacaan, bukan di
dalam satu pembacaan: `ship_id` dan `device_id` identik di setiap baris, nama
kunci JSON berulang, dan nilai sensor bertetangga hampir sama.

| Pendekatan | byte/titik | 30 hari |
|---|---|---|
| JSON polos (skema lama) | 352 | 79,4 GB |
| gzip per pembacaan | 220 | 49,7 GB |
| Tanpa ship/device id, gzip per pembacaan | 156 | 35,2 GB |
| **gzip per chunk 100 pembacaan** | **14,4** | **3,7 GB** |

Karena itu satu baris outbox kini menyimpan **satu chunk** — sekelompok pembacaan
berurutan dari satu putaran polling — bukan satu pembacaan. Angka 25 byte/titik
di atas adalah hasil akhirnya di disk, termasuk indeks dan overhead halaman
SQLite.

Konsekuensinya, chunk menjadi satuan state sekaligus granularitas terkecil sebuah
batch: `Batcher.max_records` dihormati pada batas chunk, tidak pernah memotong di
tengahnya. Memotong chunk akan menandai seluruhnya terkirim padahal sebagiannya
tidak — kehilangan data tanpa jejak.

## Yang belum diuji

Dinyatakan terbuka, bukan dianggap selesai:

- **Soak 7 hari.** Belum dijalankan. Kebocoran memori pada laju 1 Hz baru
  terlihat setelah berjam-jam, dan test terpanjang saat ini berdurasi detik.
- **Chaos sungguhan.** Mati listrik (bukan `kill -9`), disk penuh, dan jam
  melompat mundur diuji secara logis, bukan dengan mencabut daya perangkat.
- **Benchmark di Raspberry Pi.** Semua angka berasal dari mesin pengembangan.
- **Playwright E2E.** UI diuji lewat komponen, axe, dan pemeriksaan browser
  manual — belum ada alur pengguna otomatis lintas halaman.
- **Evaluasi alert.** Belum ada (carry-over Phase 5), jadi tidak ada test alert.
- **Load test terhadap central sungguhan.** Uji beban mengukur sisi edge dan
  bentuk data; laju ingest central di bawah 70 kapal serentak belum diukur.
