# 06 — Peta Jalan Implementasi

Setiap phase menghasilkan sesuatu yang bisa diverifikasi. Tidak ada phase yang dimulai
sebelum phase sebelumnya memenuhi kriteria selesainya dan user memberi lampu hijau.

| Phase | Cakupan | Kriteria selesai |
|---|---|---|
| **0 — Arsitektur** ✅ | Penilaian, struktur, alur data, batas DB/API, protokol sync, roadmap | Kumpulan dokumen ini, sudah direview |
| **1 — Fondasi** | Kerangka monorepo, `contracts/` (JSON Schema → Pydantic + TS), tooling (uv, ruff, mypy, pytest, pnpm, eslint), pre-commit, CI, stack dev `docker-compose` (Postgres + InfluxDB) | `make check` hijau; contracts menghasilkan kedua bahasa; CI lolos meski implementasinya masih kosong |
| **2 — Collector + Mock LPA** | `ProtocolAdapter` ABC, `MockLPAAdapter`, `SimulatorAdapter`, parser, validator, penjadwalan collector, config manager | Simulator menghasilkan `Reading` tervalidasi pada 1 Hz untuk 60 sinyal; unit + contract test ≥ 80 %; soak 24 jam dengan memori datar |
| **3 — Storage lokal** ✅ | Penyimpanan InfluxDB lokal, enkoder line protocol, buffer percobaan ulang berbatas, kebijakan retensi, penjagaan kardinalitas, storage health check | InfluxDB mati tidak menghentikan akuisisi ✅; timestamp pengukuran bertahan melewati buffering ✅; 4xx vs 5xx dipetakan dengan benar ✅. **Ditunda ke Phase 4:** outbox persisten, counter sequence yang bertahan, write barrier |
| **4 — Sync Engine** ✅ | Outbox SQLite (write barrier, sequence persisten), Batcher, TransportAdapter + NetworkTransport/FileExportTransport, NetworkManager, prioritas, karantina, export USB + manifest | Resume setelah putus di tengah transfer ✅; kiriman ulang batch committed tidak menggandakan ✅; export USB terverifikasi dan tidak menghapus data lokal ✅; data pending tidak pernah dipangkas ✅ |
| **5 — API + storage central** | Modular monolith, migrasi Alembic, bidang ingest (session/chunk/commit/ACK), sync-state, provisioning, gateway telemetry | Edge sungguhan → central sungguhan berhasil sync; idempotensi dan resume dibuktikan lewat integration test; coverage ≥ 80 % |
| **6 — Fleet Dashboard** ✅ | Design system (palet SPIL), ikhtisar armada, pencarian/filter, detail kapal 7 tab, chart uPlot (RPM/fuel/tekanan/suhu), alert, kesehatan sistem | 70 kapal ter-render tanpa error console ✅; bundle 90,9 kB gzip (budget 300) ✅; axe tanpa pelanggaran ✅; kontras WCAG diverifikasi terpisah ✅; responsif 600–1600 px ✅ |
| **7 — Edge Console** ✅ | FastAPI + Jinja2 + JS sendiri (bukan HTMX): 7 halaman — ikhtisar, sensor, sync, ekspor, jaringan, log, konfigurasi | Halaman 3–9 KB ✅; JS 2,3 KB, CSS 9 KB ✅; bekerja penuh tanpa JavaScript ✅; setiap halaman tetap tampil saat komponennya rusak ✅; rahasia tidak bocor di halaman mana pun ✅ |
| **8 — Integrasi E2E** ✅ | Runtime agent terpadu, tool usb-import, harness 3 kapal, 12 skenario | Semua 12 skenario lulus terhadap Postgres + InfluxDB sungguhan ✅; tidak ada data hilang ✅; tidak ada duplikat ✅; resume bekerja ✅; identitas kapal tidak tertukar ✅ |
| **9 — Pengujian** ✅ | Strategi 12 kategori, simulator telemetry sintetis, benchmark 70x100, uji beban, uji keamanan | 613 test Python + 53 TS ✅; benchmark diukur bukan dihitung ✅; **temuan: outbox tanpa kompresi butuh 79 GB untuk 30 hari offline** → diperbaiki di Phase 10. Belum: soak 7 hari, chaos fisik, benchmark di Pi — lihat docs/testing-strategy.md |
| **10 — Pengerasan produksi** ✅ | Kompresi outbox per chunk (79,4 → 6,5 GB) ✅, watchdog systemd terikat akuisisi ✅, unit systemd + installer + rollback ✅, rotasi log journald ✅, skrip backup terverifikasi ✅, pemantauan disk ✅, TLS + rate limit nginx ✅, perbaikan IP audit di balik proxy ✅, CLI admin kredensial ✅, 9 runbook operasional ✅ | Sembilan runbook tertulis dan setiap perintah di dalamnya diverifikasi ada ✅; watchdog diuji ✅; migrasi skema outbox diuji ✅. **Belum dilatih di perangkat keras sungguhan:** restore dari backup, uji cabut daya di Pi, soak 7 hari |

## Yang tersisa setelah Phase 10

Semua sudah tertulis dan teruji di CI, tetapi tiga hal hanya bisa dituntaskan pada
perangkat keras sungguhan:

1. **Latihan restore dari backup.** Skrip pencadangan memverifikasi hasilnya
   (`integrity_check`, `pg_restore --list`), tetapi pemulihan penuh ke server
   terpisah belum pernah dijalankan. Cadangan yang belum pernah dipulihkan baru
   sebuah asumsi.
2. **Uji cabut daya di Raspberry Pi.** Prosedurnya ada di
   [operations/02](../operations/02-provisioning-raspberry-pi.md#7-verifikasi-sebelum-dikirim);
   fsync di macOS tercatat 0,04 ms, sementara SD card Pi berada di kisaran 10–50 ms.
3. **Soak 7 hari dan benchmark di Pi.** Angka kapasitas saat ini diukur di mesin
   pengembangan; rasionya seharusnya berlaku, tetapi laju absolutnya tidak.

Selain itu, dua hal yang sudah diketahui sejak phase sebelumnya masih terbuka:
**evaluasi alert** (tabel, API, dan UI-nya ada, tetapi tidak ada yang
membandingkan telemetry dengan `alert_rules`) dan **penyimpanan log yang bisa
di-query** di kapal (halaman Log di Console masih kosong; log hanya ada di
journald).

## Lintas-phase, mulai dari Phase 1

- Logging JSON terstruktur dengan correlation ID (`batch_id` menyambung edge → central → dashboard)
- Metrik Prometheus di edge maupun central
- Setiap modul mencapai coverage ≥ 80 % sebelum phase-nya ditutup
- `contracts/CHANGELOG.md` diperbarui setiap kali format wire berubah

## Penghambat yang mendesak

**Memutuskan jalur baca dari LP-A104.** Manual sudah ada, tetapi justru mengungkap bahwa
perangkat ini tidak mendokumentasikan cara apa pun bagi sistem luar untuk membaca datanya.
Ada enam kandidat jalur di [`LP-A104.md`](../hardware/LP-A104.md) yang harus dipersempit.

Ini tidak menghambat Phase 1–9 (simulator menutupinya), tetapi **menghambat commissioning
produksi** — dan sekarang lebih mendesak, karena beberapa kandidat jalur menuntut perangkat
keras tambahan (konverter RS-485, tap bus) yang harus dipesan untuk 70 kapal.

Yang paling cepat menjawabnya: **file project atDesigner/atLogic yang sedang berjalan di
kapal**, karena itu langsung menunjukkan sensor apa yang dibaca, lewat channel mana, dan
disimpan di device UW mana.

## Pertanyaan terbuka untuk user

1. **Model, RAM, dan media penyimpanan Raspberry Pi** yang saat ini dipakai? InfluxDB 2.x
   butuh OS 64-bit + arm64; SD card tidak akan bertahan menghadapi penulisan terus-menerus.
2. **Hosting central** — on-premise, cloud, atau data center SPIL? Memengaruhi TLS, backup, IaC.
3. **Sistem SPIL yang sudah ada** yang perlu diintegrasikan (ERP, voyage management, SSO/LDAP)?
4. **Durasi offline terburuk yang realistis** — apakah 30 hari sudah tepat, atau lebih lama?
5. **Budget kuota data seluler** per kapal per bulan?
6. **Ekspektasi alerting** — email/SMS/WhatsApp, dan siapa penerimanya?
7. **Kewajiban retensi data** — ada ketentuan regulasi atau kontrak?
8. **Design system / brand guideline SPIL** yang sudah ada, di luar referensi website?
