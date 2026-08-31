# FleetView — Sistem Monitoring Kapal SPIL

Platform telemetry IoT offline-first untuk armada 70+ kapal.

Data selalu tersimpan di kapal terlebih dahulu. Internet bukan dependency —
LAN, Wi-Fi, seluler, dan USB semuanya hanya transport yang bisa saling menggantikan.

## Status

**Phase 1 — Fondasi.** Belum ada logic bisnis: tidak ada collector sensor,
tidak ada sync engine, tidak ada dashboard. Lihat
[peta jalan](docs/architecture/06-roadmap.md).

## Persiapan

Butuh [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/), dan Docker.

```bash
make setup     # pasang dependency Python + Node
make up        # nyalakan Postgres + InfluxDB untuk pengembangan
make check     # lint, type check, test, cek drift schema
```

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

## Menjalankan

```bash
# Edge Agent (Console di http://127.0.0.1:8080)
uv run fleetview-edge --config edge/agent/config/edge.example.yaml

# Central API (docs di http://localhost:8000/docs saat development)
uv run fleetview-central

# Dashboard
pnpm --filter @fleetview/central-web dev
```

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
