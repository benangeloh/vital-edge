# 01 — Struktur Repository

Satu monorepo. Tiga deployable (`edge-agent`, `central-api`, `central-web`) ditambah satu
package `contracts` bersama yang menjadi **satu-satunya** titik kopling antara edge dan central.

```
spil-monitoring-kapal/
│
├── shared/                          # DIPAKAI KEDUA SISTEM
│   ├── contracts/                   #   fleetview-contracts + @fleetview/contracts
│   │   ├── src/fleetview_contracts/ #     model Pydantic — SUMBER kebenarannya
│   │   ├── schemas/                 #     JSON Schema, DIGENERATE dari model
│   │   ├── ts/                      #     tipe TypeScript untuk dashboard
│   │   ├── scripts/                 #     export_schemas.py (+ mode --check untuk CI)
│   │   ├── tests/
│   │   └── CHANGELOG.md             #     riwayat schema_version + aturan kompatibilitas
│   └── common/                      #   fleetview-common
│       └── src/fleetview_common/    #     logging, error, correlation ID, waktu
│
├── edge/                            # SISTEM 1 → di-deploy ke setiap kapal
│   ├── agent/                       #   fleetview-edge-agent
│   │   ├── src/fleetview_edge/
│   │   │   ├── collector/           #     penjadwalan, loop polling, sampling
│   │   │   ├── protocol/            #     ProtocolAdapter ABC + mock + simulator + lp_a104/
│   │   │   ├── parser/              #     frame mentah -> Reading bertipe
│   │   │   ├── validator/           #     range, laju perubahan, staleness -> flag quality
│   │   │   ├── storage/             #     outbox (SQLite) + timeseries (InfluxDB) + retensi
│   │   │   ├── sync/                #     SyncEngine, batcher, transports/{http,file}
│   │   │   ├── network/             #     probing link, pemilihan transport, budget kuota
│   │   │   ├── health/              #     metrik sistem+aplikasi, diagnosis mandiri
│   │   │   ├── export/              #     export USB/HDD + verifikasi
│   │   │   └── config/              #     pemuatan config berlapis, schema, hot reload
│   │   ├── config/edge.example.yaml #     contoh config per kapal
│   │   └── tests/
│   ├── console/                     #   fleetview-edge-console
│   │   └── src/fleetview_console/   #     FastAPI + Jinja2 + HTMX, di-mount agent
│   └── deploy/                      #   unit systemd, install.sh, bootstrap influx
│
├── central/                         # SISTEM 2 → di-deploy ke pusat
│   ├── api/                         #   fleetview-central-api (modular monolith)
│   │   ├── src/fleetview_central/
│   │   │   ├── modules/             #     identity, provisioning, fleet, ingest,
│   │   │   │                        #     telemetry, syncstate, config, alerts, audit
│   │   │   ├── platform/            #     db, influx, auth, error, logging, settings
│   │   │   └── http/                #     router, envelope, error handler, OpenAPI
│   │   ├── migrations/              #   Alembic
│   │   └── tests/
│   └── web/                         #   @fleetview/central-web (Vite + React)
│
├── tools/                           # simulator armada, CLI import USB
├── deploy/                          # docker-compose (dev), IaC central
└── docs/{architecture,hardware,runbooks}/
```

Tiga folder teratas menjawab pertanyaan "ini bagian yang mana": `shared/` ikut ke
mana-mana, `edge/` ke kapal, `central/` ke pusat. Masing-masing punya README
sendiri. Nama distribusi memakai prefiks yang sama (`fleetview-edge-*`,
`fleetview-central-*`) sehingga terbaca juga dari daftar dependency, bukan hanya
dari struktur folder.

## Aturan batas antar-modul

1. **`contracts/` tidak bergantung pada apa pun.** Semua yang lain boleh bergantung padanya.
2. **Edge tidak pernah meng-import central; central tidak pernah meng-import edge.**
   Keduanya hanya bertemu di `contracts/`.
3. **Antar-modul central berkomunikasi lewat service interface publik**, tidak pernah dengan
   meng-import model ORM milik modul lain atau menyentuh tabel milik modul lain. Inilah yang
   menjaga monolith tetap modular dan membuat pemisahan di masa depan tetap murah.
4. **`ingest` tidak boleh meng-query `telemetry` untuk keperluan dashboard, dan jalur baca
   dashboard tidak boleh menulis.** Bidang ingest dan bidang query dipisahkan di level modul.
5. **Protocol adapter adalah modul daun.** Tidak ada bagian di luar
   `edge/agent/.../protocol/` yang tahu bahwa register address itu ada.
6. Batas ukuran file 800 baris, fungsi di bawah 50 baris — ditegakkan di CI.
