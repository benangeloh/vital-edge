# 00 — Penilaian Arsitektur

**Status:** Phase 0 (desain saja, belum ada implementasi)
**Tanggal:** 2026-08-31

## 1. Hasil pemeriksaan repository

| Item | Temuan |
|---|---|
| Kondisi repository | Kosong. `git init` baru, nol commit, nol file. |
| Kode / pekerjaan sebelumnya | Tidak ada. Greenfield. |
| Dokumentasi LP-A104 | **Tersedia sejak 2026-08-31.** User manual 151 halaman + lembar instruksi produk ada di `docs/hardware/`. |
| Pilihan teknologi yang sudah ada | Belum ada. Semua pilihan di bawah adalah rekomendasi baru. |

**Konsekuensinya:** perangkat aslinya adalah **Autonics LP-A104**, bukan "ATONICS LPA-A104",
dan ia **bukan protocol gateway** melainkan HMI dengan PLC logic terintegrasi. Manual tidak
mendokumentasikan satu pun cara bagi sistem luar untuk membaca data darinya. Jadi pertanyaan
terbukanya bergeser dari "apa register map-nya" menjadi "lewat jalur mana Pi mengambil data".
Rinciannya, termasuk enam kandidat jalur integrasi, ada di
[`docs/hardware/LP-A104.md`](../hardware/LP-A104.md). Kita tetap membangun di belakang
interface `ProtocolAdapter` dengan mock + simulator.

## 2. Toolchain lokal yang sudah diverifikasi

`node 22.23`, `pnpm 10.30`, `docker 29.1`, `uv 0.11`. `python3` bawaan sistem adalah
**3.9.6 — terlalu lama**; `uv` akan mengunci proyek ke Python 3.11 (sama dengan Python
bawaan Raspberry Pi OS Bookworm, sehingga deployment ke edge jadi sederhana).

## 3. Perkiraan skala & beban (target desain)

| Dimensi | Nilai |
|---|---|
| Jumlah kapal | 70+ (ruang tumbuh desain: 200) |
| Sinyal per kapal | ~40–80 (harus bisa bertambah tanpa ubah kode) |
| Sample rate | 1 Hz umumnya, 0,1 Hz untuk sinyal lambat |
| Puncak titik data/kapal/hari | ~80 × 86.400 ≈ 7 juta |
| Ingest armada kondisi normal | ~70 × 80 = 5.600 titik/detik — **kecil**; dengan batching jadi ringan sekali |
| Skenario offline terburuk | 30 hari ⇒ ~200 juta titik menumpuk di satu kapal |

Yang menentukan sizing bukan throughput, tapi **buffer offline 30 hari**. Itulah yang
memaksa adanya kompresi + kebijakan retensi lokal yang disiplin, dan itu pula alasan kenapa
edge butuh SSD, bukan SD card.

## 4. Keputusan teknologi

| # | Keputusan | Pilihan | Alasan | Yang ditolak |
|---|---|---|---|---|
| D1 | Bahasa di edge | **Python 3.11 + asyncio** | Ekosistem protokol industri paling lengkap (pymodbus, pyserial, canopen); bahasa yang sama dengan central ⇒ satu package `contracts` bisa dipakai bersama; iterasi cepat. Pemakaian RAM ~120 MB masih wajar di Pi 4 GB. | Go (lebih hemat, tapi library fieldbus lebih lemah dan menambah satu bahasa untuk dirawat); Node (dukungan fieldbus buruk) |
| D2 | Telemetry store di edge | **InfluxDB 2.7 OSS (lokal)** | Diminta di spesifikasi. Build arm64 sudah matang, client Python stabil, retention policy sudah built-in. | InfluxDB 3 Core — lebih baru, layak, tapi ditinjau ulang di Phase 10; TimescaleDB (spesifikasi melarang Postgres untuk telemetry di edge) |
| D3 | Journal sync di edge | **SQLite (mode WAL)** | Outbox butuh ACID, sequence yang naik monoton, dan state per batch yang bisa berubah (`pending → in_flight → acked`). InfluxDB tidak menyediakan satu pun dari itu. **SQLite menyimpan pembukuan batch dan payload terserialisasi, bukan telemetry yang bisa di-query** — jadi D2 tetap menjadi telemetry store resmi. | File segment append-only buatan sendiri (lebih banyak kode, jaminan sama); memakai time-range query InfluxDB sebagai outbox (rapuh terhadap clock skew dan data terlambat) |
| D4 | Edge Console | **FastAPI + Jinja2**, berjalan satu proses. *(Revisi Phase 7: HTMX diganti ~2 KB JS sendiri — kapal tidak punya internet, jadi HTMX harus di-vendor sebagai blob minified yang tidak bisa diverifikasi isinya sebelum dikirim ke 70 perangkat.)* | "SANGAT ringan": tidak perlu runtime Node di Pi, tidak perlu build step, total JS ~14 KB, tetap enak dipakai lewat LAN kapal yang lambat. Ini alat ops/troubleshooting, bukan analytics. | SPA React/Vue (toolchain build + bundle di Pi untuk 12 panel status tidak sepadan) |
| D5 | Backend central | **Modular monolith FastAPI** | Sesuai mandat spesifikasi. Satu deployable, batas antar-modul ditegakkan di level kode agar pemisahan tetap mungkin di kemudian hari. | Microservices (eksplisit di luar scope pada skala ini) |
| D6 | Relational central | **PostgreSQL 16** | Sesuai mandat spesifikasi. |
| D7 | Telemetry central | **InfluxDB 2.7 OSS** | Sesuai mandat spesifikasi. Simetris dengan edge ⇒ satu jalur tulis, satu client library. |
| D8 | Dashboard | **Vite + React 19 + TypeScript + TanStack Query + Tailwind** | Alat ops internal: tidak butuh SEO, tidak butuh SSR. Vite menjaga bundle tetap di bawah budget 300 KB. | Next.js (mesin SSR-nya tidak terpakai di sini) |
| D9 | Chart | **uPlot** untuk time-series, SVG buatan sendiri untuk sparkline/gauge | Ukuran ~45 KB, sanggup render 100 ribu titik dengan mulus — alat yang tepat untuk console telemetry. Sekaligus menghindari tampilan dashboard generik khas library chart bawaan. | Recharts/Chart.js (lebih berat, lebih lambat pada kepadatan data telemetry) |
| D10 | Transport | **HTTPS + chunked upload yang resumable**, satu Sync Engine dengan transport yang bisa ditukar | LAN / Wi-Fi / 4G semuanya hanyalah HTTP lewat rute berbeda. USB menjadi `FileTransport` yang menulis batch envelope yang identik. | MQTT (tidak punya resume bawaan untuk backlog besar; menambah broker); rsync (tidak punya semantik ACK di level aplikasi) |
| D11 | Autentikasi device | Kredensial per-device → JWT berumur pendek, lewat TLS. mTLS opsional per lokasi. | Mudah dioperasikan untuk 70 kapal; kredensial bisa dicabut dan dirotasi dari pusat. | mTLS saja (mengurus siklus hidup sertifikat di 70 Pi terpencil tanpa internet itu menyakitkan) |

## 5. Kebutuhan hardware (edge) — perlu dikonfirmasi ke customer

- **Raspberry Pi 4 (4 GB) atau Pi 5.** InfluxDB 2.x hanya menyediakan build **arm64** —
  tidak ada build untuk ARM 32-bit.
- **Raspberry Pi OS 64-bit (Bookworm) wajib**, bukan opsional.
- **SSD atau eMMC industrial, minimal 128 GB.** SD card konsumer tidak akan bertahan
  menghadapi penulisan time-series terus-menerus; ini adalah penyebab kegagalan paling
  sering pada sistem sejenis.

  Terukur setelah Phase 10: outbox memakai 0,22 GB/hari per kapal, sehingga
  skenario offline 30 hari menghabiskan **6,5 GB** — nyaman di dalam 128 GB
  bersama InfluxDB lokal beretensi 90 hari. Sebelum payload outbox dikompresi
  per chunk, angka yang sama adalah 79 GB, yang tidak akan muat. Lihat
  [`../testing-strategy.md`](../testing-strategy.md#temuan-kapasitas-outbox-pada-offline-panjang).
- Modul RTC atau jam yang disinkronkan GPS. Integritas timestamp menopang seluruh protokol
  sync — lihat [`05-sync-protocol.md`](05-sync-protocol.md) §7.
- UPS / rangkaian graceful shutdown. Listrik kapal sering mati-nyala; pemutusan mendadak
  saat fsync dapat merusak TSM store InfluxDB.

## 6. Risiko utama

| Risiko | Dampak | Mitigasi |
|---|---|---|
| **Jalur baca dari LP-A104 belum terkonfirmasi** | Menghambat akuisisi data nyata; berpotensi menuntut perangkat keras tambahan (konverter/tap RS-485) untuk 70 kapal | Interface adapter + mock + simulator sekarang; jalur integrasi harus diputuskan sebelum Phase 2 selesai. Lihat [`LP-A104.md`](../hardware/LP-A104.md) |
| Clock skew / jam mundur di edge | Data ganda atau tidak bisa diurutkan | Sequence monoton yang jadi patokan urutan, bukan jam dinding; RTC wajib; skew dilaporkan di health |
| SD card aus | Kehilangan seluruh data di satu kapal | Wajib SSD; SMART disk dipantau Health Monitor |
| Tekanan RAM InfluxDB di Pi | OOM-kill, data hilang | Batasi ukuran cache; kebijakan watermark disk; jangan pernah hapus baris outbox yang belum di-ACK |
| Banjir backlog 30 hari saat reconnect | Central kewalahan, kuota 4G kapal habis | Drain dibatasi rate, budget per transport, backpressure server lewat `429 + Retry-After` |
| Konfigurasi 70 kapal jadi tidak seragam | Bug yang tidak bisa direproduksi | Config berversi, `config_version` dilaporkan di setiap heartbeat |
