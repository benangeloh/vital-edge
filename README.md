# FleetView — Sistem Monitoring Kapal SPIL

Platform telemetry IoT offline-first untuk armada 70+ kapal.

Data selalu tersimpan di kapal terlebih dahulu. Internet bukan dependency —
LAN, Wi-Fi, seluler, dan USB semuanya hanya transport yang bisa saling menggantikan.

## Status

**Phase 10 — Pengerasan produksi, selesai.** Seluruh jalur berfungsi ujung ke
ujung: akuisisi, penyimpanan lokal, sync, ingest central, dashboard, Console
kapal, dan artefak deployment.

Satu hal menghambat commissioning di kapal sungguhan: **jalur baca dari LP-A104
belum terkonfirmasi** — lihat [status hardware](docs/hardware/LP-A104.md).
Sampai itu beres, akuisisi dijalankan dengan adapter simulator.

Lihat [peta jalan](docs/architecture/06-roadmap.md) untuk yang tersisa.

## Mulai cepat

Butuh [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/), dan Docker.

```bash
make setup     # sekali saja: dependency Python + Node
make dev       # jalankan semuanya
```

`make dev` menyalakan database, menerapkan migrasi, mendaftarkan kapal simulasi
beserta kredensialnya, lalu menjalankan central + Edge Agent + dashboard dengan
log berlabel di satu terminal. Ctrl-C menghentikan semuanya dengan rapi.

```
Dashboard  http://localhost:5173   masuk: operator / bima2005
Console    http://localhost:8080
API docs   http://localhost:8000/docs
```

Rinciannya, termasuk cara menjalankan sebagian saja:
[Cara Menjalankan](docs/operations/00-menjalankan.md).

## Struktur

Ini **monorepo** berisi **dua sistem yang di-deploy terpisah**, plus kode yang
dipakai keduanya. Satu repo karena `shared/contracts` harus identik di kedua sisi —
kalau dipisah jadi dua repo, format wire bisa menyimpang tanpa ada yang menangkapnya.

```
shared/     Dipakai KEDUA sistem — format wire, logging, error, waktu
├── contracts/     fleetview-contracts   + @fleetview/contracts
└── common/        fleetview-common

edge/       SISTEM 1 → di-deploy ke setiap kapal (Raspberry Pi)
├── agent/         fleetview-edge-agent      collector, storage, sync
└── console/       fleetview-edge-console    UI operasional, jalan di proses agent

central/    SISTEM 2 → di-deploy ke pusat
├── api/           fleetview-central-api     modular monolith
└── web/           @fleetview/central-web    dashboard (file statis)

tools/      Simulator, CLI import USB (Phase 2 & 4)
docs/       Arsitektur dan catatan hardware
```

Tiap folder sistem punya README-nya sendiri:
[shared/](shared/README.md) · [edge/](edge/README.md) · [central/](central/README.md)

## Yang berjalan di mana

| Lokasi | Proses |
|---|---|
| Tiap kapal (×70) | `fleetview-edge.service` (satu proses: collector + sync + console) + `influxdb` |
| Pusat | `fleetview-central-api` + `postgres` + `influxdb` + nginx untuk file statis dashboard |

Bukan microservice. Dashboard bukan proses aplikasi — hasil `vite build`, disajikan
sebagai file statis.

## Menjalankan sebagian

```bash
make dev-central   # central saja
make dev-ship      # kapal simulasi saja (butuh central hidup)
make dev-status    # apa yang sedang jalan
make dev-reset     # hapus data kapal simulasi, di kedua sisi
```

Kalau menjalankan Edge Agent secara manual, pakai `edge.dev.yaml`, **bukan**
`edge.example.yaml`. Yang kedua adalah template produksi: adapter `lp_a104`
sengaja gagal keras karena jalur bacanya belum terkonfirmasi, dan
`environment: production` menolak start bila config belum lengkap.

## Perintah

| Perintah | Fungsi |
|---|---|
| `make check` | Semua pemeriksaan — inilah yang dijalankan CI |
| `make test` / `make test-cov` | Test, dengan atau tanpa coverage |
| `make fmt` / `make lint` | Format / periksa kode Python |
| `make typecheck` | mypy strict |
| `make schemas` | Regenerate JSON Schema dari model Pydantic |
| `make up` / `make down` | Stack dev Postgres + InfluxDB |

## Dokumentasi

Mulai dari [docs/README.md](docs/README.md).

| Kebutuhan | Dokumen |
|---|---|
| Menjalankan sistem | [Cara menjalankan](docs/operations/00-menjalankan.md) |
| **Memasang kapal baru (alur lengkap)** | **[Onboarding kapal](docs/operations/07-ship-onboarding.md)** |
| Memasang di Raspberry Pi | [Provisioning](docs/operations/02-provisioning-raspberry-pi.md) |
| Memasang server pusat | [Deployment central](docs/operations/03-central-deployment.md) |
| Ada yang tidak beres | [Troubleshooting](docs/operations/09-troubleshooting.md) |

Dua hal yang paling perlu diketahui sebelum menyentuh kode:

- [Protokol sync](docs/architecture/05-sync-protocol.md) — mengapa sebuah pembacaan
  tidak bisa hilang, dan mengapa `sequence` yang jadi patokan urutan, bukan jam.
- [Status LP-A104](docs/hardware/LP-A104.md) — **jalur baca dari perangkat lapangan
  belum terkonfirmasi.** Jangan menulis adapter hardware sungguhan sebelum ini beres.

## Konvensi

- Python 3.11 (sama dengan Raspberry Pi OS Bookworm), ruff + mypy strict
- Rahasia lewat environment variable, tidak pernah lewat berkas di repo
- Timestamp selalu membawa timezone; datetime naif ditolak di batas sistem
- Coverage minimal 80%
