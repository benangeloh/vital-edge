# edge/ — SISTEM 1: di-deploy ke setiap kapal

Berjalan di Raspberry Pi di atas kapal, 24/7, sering tanpa internet.

| Paket | Distribusi | Peran |
|---|---|---|
| `agent/` | `fleetview-edge-agent` | Collector, storage, sync engine, health, export |
| `console/` | `fleetview-edge-console` | UI operasional ringan untuk teknisi di kapal |

## Satu proses, bukan dua

Console adalah paket terpisah — batas dan test-nya sendiri — tetapi **berjalan di
dalam proses agent**. Raspberry Pi tidak perlu proses kedua, port kedua, dan unit
systemd kedua hanya untuk dua belas panel status. Yang di-deploy ke kapal:

```
fleetview-edge.service    satu proses: collector + storage + sync + console
influxdb.service          daemon pihak ketiga
```

SQLite embedded di dalam proses agent, tidak punya daemon.

## Satu build untuk 70 kapal

Artifact-nya identik di semua kapal. Yang membedakan hanya `/etc/fleetview/edge.yaml`
dan rahasia lewat environment variable. Karena itu `ship_id` wajib dan tanpa default:
agent menolak start daripada mengirim data atas nama kapal lain.

```bash
uv run fleetview-edge --config edge/agent/config/edge.example.yaml
```

## Sebelum menulis adapter hardware

Baca [docs/hardware/LP-A104.md](../docs/hardware/LP-A104.md) lebih dulu.
**Jalur baca dari perangkat lapangan belum terkonfirmasi.** Jangan mengarang
register address — data yang terlihat masuk akal tapi salah adalah kegagalan
terburuk untuk sistem monitoring, karena tidak menimbulkan gejala.
