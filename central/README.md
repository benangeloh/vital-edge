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

```
fleetview-central-api    satu proses, stateless, bisa diskalakan jadi N replika
postgres                 metadata relational
influxdb                 telemetry time-series
nginx / Caddy            menyajikan hasil build web/ — file statis, bukan proses Node
```

```bash
uv run fleetview-central               # API
pnpm --filter @fleetview/central-web dev   # dashboard
```
