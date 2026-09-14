# central/ — SISTEM 2: di-deploy ke pusat

Menerima data dari 70+ kapal, menyimpannya, dan menyajikannya ke operator.

| Paket | Distribusi | Peran |
|---|---|---|
| `api/` | `fleetview-central-api` | Modular monolith: ingest, fleet, telemetry, sync-state, alerts, identity, audit |
| `web/` | `@fleetview/central-web` | Dashboard armada (Vite + React) |

## Modular monolith, bukan microservice

Modul di `api/src/fleetview_central/modules/` adalah **batas kode, bukan batas
jaringan** — mereka saling memanggil lewat pemanggilan fungsi biasa, dalam satu proses.

Aturan yang menjaga pemisahan tetap mungkin di kemudian hari: modul berkomunikasi
lewat service interface publik, tidak pernah dengan mengimpor model ORM milik modul
lain atau menyentuh tabel milik modul lain.

Untuk 70 kapal (~5.600 titik/detik), memecahnya jadi microservice sekarang hanya
menambah biaya operasional tanpa manfaat.

## Yang berjalan di pusat

Seluruh stack produksi diorkestrasi lewat Docker Compose
(`docker-compose.prod.yml`) — lihat
[03 — Deployment Central Platform](../docs/operations/03-central-deployment.md):

```
docker compose -f docker-compose.prod.yml    orkestrasi seluruh stack produksi
├── api        fleetview-central-api, stateless, bisa diskalakan jadi N replika
├── proxy      nginx: TLS, rate-limit, file statis web/, reverse proxy ke api
├── postgres   metadata relational
├── influxdb   telemetry time-series
└── migrate    job alembic, jalan sekali tiap deploy sebelum api start
```

Untuk pengembangan lokal (bukan produksi), lihat
[00 §A](../docs/operations/00-menjalankan.md#a--di-komputer-sendiri):

```bash
uv run fleetview-central               # API
pnpm --filter @fleetview/central-web dev   # dashboard
```
