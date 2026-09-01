# FleetView — Sistem Monitoring Kapal SPIL

Platform telemetry IoT offline-first untuk armada 70+ kapal.

## Dokumen arsitektur

| Dokumen | Isi |
|---|---|
| [00 — Penilaian](architecture/00-assessment.md) | Temuan repo, perkiraan skala, keputusan teknologi, kebutuhan hardware, risiko |
| [01 — Struktur repository](architecture/01-repository-structure.md) | Tata letak monorepo, aturan batas antar-modul |
| [02 — Alur data](architecture/02-data-flow.md) | Jalur akuisisi, offload, central, baca; perilaku saat gagal |
| [03 — Batas database](architecture/03-database-boundaries.md) | Empat store, schema, aturan lintas-store |
| [04 — Batas API](architecture/04-api-boundaries.md) | Bidang ingest / control / query, API Edge Console |
| [05 — Protokol sync](architecture/05-sync-protocol.md) | Batch envelope, resume, idempotensi, watermark, USB, penanganan waktu |
| [06 — Peta jalan](architecture/06-roadmap.md) | Phase 0–10 beserta kriteria selesai; pertanyaan terbuka |
| [07 — Arah desain](architecture/07-design-direction.md) | Bahasa visual, token, aturan UI |
| [Konvensi atLogic/atDesigner](hardware/konvensi-atlogic.md) | Cara menyetel panel agar cocok dengan dashboard tanpa mengubah kode |
| [Status LP-A104](hardware/LP-A104.md) | Manual sudah ada; **jalur integrasi belum terkonfirmasi** — spesifikasi, peta memori, enam kandidat jalur |
| [Strategi pengujian](testing-strategy.md) | Dua belas kategori uji, angka benchmark, temuan kapasitas |

## Dokumen operasional

Runbook untuk menjalankan sistem di produksi.

| Dokumen | Kapan dipakai |
|---|---|
| [00 — Cara menjalankan](operations/00-menjalankan.md) | Menjalankan sistem: di komputer sendiri, di Pi kapal, di server pusat |
| [01 — Deployment produksi](operations/01-deployment.md) | Memahami bentuk sistem saat berjalan; kapasitas, watchdog, strategi pembaruan |
| [02 — Provisioning Raspberry Pi](operations/02-provisioning-raspberry-pi.md) | Menyiapkan Pi baru dari nol, di darat |
| [03 — Deployment central](operations/03-central-deployment.md) | Memasang atau memutakhirkan server pusat |
| [04 — Prosedur backup](operations/04-backup.md) | Apa yang dicadangkan, dan kenapa yang lain tidak |
| [05 — Disaster recovery](operations/05-disaster-recovery.md) | Central mati, disk penuh, outbox rusak, rollback melewati migrasi |
| [06 — Penggantian perangkat](operations/06-device-replacement.md) | Raspberry Pi kapal rusak |
| **[07 — Onboarding kapal](operations/07-ship-onboarding.md)** | **Alur lengkap kapal baru: daftar di dashboard → siapkan Pi → pasang → data masuk** |
| [08 — Decommissioning kapal](operations/08-ship-decommissioning.md) | Kapal dijual atau pensiun |
| [09 — Troubleshooting](operations/09-troubleshooting.md) | Ada yang tidak beres |

## Tiga aplikasi

- **Edge Agent** — Python 3.11, berjalan di Raspberry Pi di setiap kapal. Mengumpulkan,
  memvalidasi, menyimpan lokal, lalu menyinkronkan saat ada kesempatan.
- **Edge Console** — FastAPI, dilayani oleh agent itu sendiri. Berfungsi penuh tanpa
  JavaScript. Untuk operasional
  dan troubleshooting di kapal.
- **Central Platform** — modular monolith FastAPI + PostgreSQL + InfluxDB, dengan dashboard
  armada berbasis Vite/React.

## Hal yang tidak bisa ditawar

1. Sebuah pembacaan harus durable secara lokal sebelum apa pun terjadi. Internet tidak pernah
   menjadi dependency.
2. Hanya ada satu Sync Engine; LAN, Wi-Fi, seluler, dan USB adalah transport yang bisa saling
   menggantikan.
3. Sync bersifat at-least-once di jaringan dan effectively-exactly-once di storage.
4. Data yang sudah tersimpan lokal tidak pernah hilang hanya karena sync gagal.
5. Satu build Edge Agent yang sama dideploy ke 70+ kapal; yang berbeda hanya konfigurasi
   dan identitasnya.
