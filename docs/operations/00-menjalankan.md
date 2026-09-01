# 00 — Cara Menjalankan

Tiga konteks yang berbeda, jangan dicampur:

| Konteks | Untuk apa | Bagian |
|---|---|---|
| **Komputer sendiri** | Mencoba sistem, mengembangkan | [A](#a--di-komputer-sendiri) |
| **Raspberry Pi kapal** | Produksi | [B](#b--di-raspberry-pi-kapal) |
| **Server central** | Produksi | [C](#c--di-server-central) |

Bagian A memakai adapter **simulator**. Bagian B dan C mengandaikan sistem sudah
terpasang lewat [02](02-provisioning-raspberry-pi.md) dan
[03](03-central-deployment.md) — dokumen ini tentang menjalankan sehari-hari,
bukan memasang.

---

# A — Di komputer sendiri

Menjalankan seluruh sistem di satu mesin: kapal simulasi, central, dan dashboard.
Tidak perlu Raspberry Pi dan tidak perlu perangkat LP-A104.

Butuh [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/), dan Docker.

## Satu perintah

```bash
make setup     # sekali saja
make dev
```

Itu saja. `make dev` mengurus seluruh langkah A1–A5 di bawah: menyalakan
Postgres dan InfluxDB lalu **menunggunya benar-benar siap** (bukan sekadar
portnya terbuka), membuat `.env`, menjalankan migrasi, mendaftarkan kapal
simulasi beserta kredensial dan operator, lalu menjalankan ketiga proses dengan
log berlabel di satu terminal.

```
Dashboard  http://localhost:5173   masuk: operator / bima2005
Console    http://localhost:8080
API docs   http://localhost:8000/docs
```

Ctrl-C menghentikan semuanya. Penghentiannya memakai SIGTERM, jalur yang sama
dengan systemd di kapal, sehingga outbox ditutup rapi tanpa transaksi menggantung.

| Perintah | Fungsi |
|---|---|
| `make dev` | central + kapal simulasi + dashboard |
| `make dev-central` | central saja |
| `make dev-ship` | kapal simulasi saja (butuh central hidup) |
| `make dev-status` | apa yang sedang jalan |
| `make dev-reset` | hapus data kapal simulasi, **di kedua sisi** |

`make dev-reset` membersihkan outbox kapal **dan** ledger di central bersamaan.
Menghapus salah satu saja membuat agent memulai nomor sequence dari 1 lagi
sementara central masih menyimpan ledger untuk rentang itu; kiriman berikutnya
ditolak `ingest.sequence_reused` — benar sebagai perilaku, membingungkan sebagai
hasil dari sekadar melakukan reset.

Bagian di bawah menjelaskan langkah yang sama secara manual: berguna saat ada
yang gagal, atau saat ingin menjalankan hanya sebagian.

## Langkah manual

## A1. Persiapan (sekali saja)

```bash
make setup                 # dependency Python + Node
cp .env.example .env       # config central untuk stack dev
make up                    # Postgres + InfluxDB lewat Docker
make migrate               # buat skema central
```

`cp .env.example .env` mudah terlewat dan akibatnya membingungkan: central hidup
dan menerima batch, tetapi setiap penulisan ke InfluxDB ditolak 401 dan kapal
melihat HTTP 503 berulang. Datanya tidak hilang — batch hanya **ditunda**, dan
akan tersetor sendiri begitu `.env` ada — tetapi mencari sebabnya memakan waktu.

## A2. Nyalakan central

```bash
uv run fleetview-central
```

Terminal ini biarkan terbuka. Verifikasi:

```bash
curl -s http://127.0.0.1:8000/api/health
# dokumentasi API interaktif: http://localhost:8000/docs
```

## A3. Daftarkan kapal simulasi dan terbitkan kredensialnya

Di terminal lain. UUID di bawah sengaja tetap dan **harus sama** dengan yang ada
di `edge/agent/config/edge.dev.yaml` — itulah gunanya `--id`.

```bash
uv run fleetview-admin ship add --name "KM Simulasi Dev" --slug SHIP-DEV \
  --id 11111111-1111-1111-1111-111111111111

uv run fleetview-admin device add --name "Edge Pi Dev" \
  --ship-id 11111111-1111-1111-1111-111111111111 \
  --id 22222222-2222-2222-2222-222222222222

uv run fleetview-admin credential issue \
  --device-id 22222222-2222-2222-2222-222222222222 --client-id ship-dev
```

Salin `secret` yang muncul — hanya ditampilkan sekali, karena di basis data ia
hanya ada sebagai hash Argon2id.

Terakhir, buat operator untuk masuk ke dashboard. Tidak ada endpoint pendaftaran
mandiri, dan itu disengaja: platform yang mengawasi 70 kapal tidak boleh punya
jalur pendaftaran terbuka. Konsekuensinya, langkah ini wajib pada instalasi baru
— tanpa itu dashboard tidak bisa dimasuki sama sekali.

```bash
uv run fleetview-admin user add --username operator
#   kata sandi diminta interaktif, supaya tidak tersimpan di riwayat shell
```

## A4. Nyalakan kapal simulasi

```bash
export FLEETVIEW_SYNC__DEVICE_CLIENT_ID=ship-dev
export FLEETVIEW_SYNC__DEVICE_SECRET=<secret dari langkah A3>

uv run fleetview-edge --config edge/agent/config/edge.dev.yaml
```

Agent akan menukar kredensial itu menjadi token sendiri, dan menukar ulang
otomatis saat kedaluwarsa.

**Jangan memakai `edge.example.yaml`.** Berkas itu template produksi: adapter
`lp_a104` sengaja gagal keras karena jalur bacanya belum terkonfirmasi, dan
`environment: production` memicu pemeriksaan kelengkapan yang akan menolak start.

Yang seharusnya terlihat di log, berurutan:

```
collector.started        adapter=simulator sensors=11
sync.token_refreshed     client_id=ship-dev
sync.batch_built         records=72 chunks=10
sync.batch_acked
```

## A5. Console kapal dan dashboard armada

```bash
# Console kapal — sudah jalan di dalam proses agent
open http://127.0.0.1:8080

# Dashboard armada, di terminal ketiga
pnpm --filter @fleetview/central-web dev
open http://localhost:5173
```

Masuk dengan operator yang dibuat di langkah A3. Dashboard mem-proxy `/api` ke
`localhost:8000`, jadi tidak perlu config tambahan.

## A6. Memastikan data benar-benar sampai

```bash
curl -s http://127.0.0.1:8000/api/v1/sync/ships/11111111-1111-1111-1111-111111111111 \
  | python3 -m json.tool
```

`last_contiguous_sequence` harus naik dan sama dengan `highest_sequence_seen`
(sama = tidak ada celah). Dari sisi kapal:

```bash
sqlite3 .devdata/outbox.db "SELECT state, SUM(record_count) FROM outbox GROUP BY state;"
```

`acked` bertambah berarti central sudah mengonfirmasi. `pending` yang selalu ada
sedikit itu normal — itu data yang baru saja diukur dan belum masuk batch.

Dan pastikan datanya benar-benar bisa dibaca kembali, bukan sekadar diterima:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"operator","password":"<kata sandi>"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["access_token"])')

curl -s "http://127.0.0.1:8000/api/v1/telemetry/latest?ship_id=11111111-1111-1111-1111-111111111111" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -20
```

## A7. Menghentikan

```
Ctrl-C di tiap terminal
make down        # matikan Postgres + InfluxDB
rm -rf .devdata  # buang data kapal simulasi
```

**Jangan jalankan `make check` sementara agent dev masih hidup.** Keduanya
memakai InfluxDB yang sama, dan telemetry simulasi akan mencemari integration
test sehingga gagal tanpa sebab yang jelas.

## Perintah pengembangan

| Perintah | Fungsi |
|---|---|
| `make check` | Semua pemeriksaan — inilah yang dijalankan CI |
| `make test` | Test Python saja |
| `make test-integration` | Terhadap Postgres + InfluxDB sungguhan |
| `make bench` | Benchmark 70 kapal × 100 sensor |
| `make up` / `make down` | Stack dev |

---

# B — Di Raspberry Pi kapal

Agent dijalankan systemd, bukan manual. Perintah di bawah adalah operasional
sehari-hari; pemasangan ada di [02](02-provisioning-raspberry-pi.md).

## B1. Status dan kendali

```bash
sudo systemctl status fleetview-agent      # baris Status: memuat angka operasional
sudo systemctl restart fleetview-agent
sudo systemctl stop fleetview-agent
sudo systemctl start fleetview-agent
```

Baris `Status:` diperbarui agent sendiri dan berbentuk seperti
`polls=8421 pending=63 record`. Angka `polls` **harus bertambah** antar
pemeriksaan; kalau diam, akuisisi macet dan watchdog akan merestart dalam 60 detik.

Perhatikan bedanya `stop` dan `disable`. `stop` menghentikan sekarang tetapi
agent hidup lagi saat reboot — itu yang diinginkan saat sekadar memeriksa
sesuatu. `disable` mencegahnya hidup kembali, dan hanya dipakai saat
[decommissioning](08-ship-decommissioning.md).

> **Selama agent berhenti, kapal tidak mengumpulkan data.** Tidak ada yang
> menampungnya di belakang layar. Hampir semua diagnosis bisa dilakukan tanpa
> menghentikannya, dan agent yang dihentikan "sebentar" adalah penyebab
> kehilangan data yang paling sering terjadi.

## B1b. Satu perintah untuk melihat semuanya

```bash
fleetview-status
```

Menggantikan empat perintah diagnosis pertama sekaligus: status layanan, apakah
polling benar-benar **maju** (dua cuplikan berjarak, karena satu angka tidak
memberi tahu apa pun), data yang belum tersetor beserta umur tertuanya, batch
yang dikarantina, ruang disk, Console, dan kesalahan terakhir.

Skrip ini tidak menghentikan dan tidak menulis apa pun.

## B2. Melihat log

```bash
sudo journalctl -u fleetview-agent -f              # ikuti langsung
sudo journalctl -u fleetview-agent --since "1 hour ago"
sudo journalctl -u fleetview-agent -o cat | jq 'select(.level=="error")'
```

Log berbentuk JSON terstruktur; `-o cat` melepas prefix journald supaya `jq`
bisa membacanya.

## B3. Memeriksa data tanpa menyentuh agent

```bash
# Berapa yang belum tersetor
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT state, SUM(record_count) FROM outbox GROUP BY state;"

# Sisa ruang
df -h /var/lib/fleetview
```

## B4. Console kapal

Berjalan di dalam proses agent, default hanya bisa diakses dari Pi itu sendiri:

```bash
curl -sf http://127.0.0.1:8080/ >/dev/null && echo ok
```

Untuk membukanya dari laptop teknisi tanpa mengubah konfigurasi, pakai
port-forward SSH — lebih aman daripada mengikat ke `0.0.0.0`, karena Console
tidak punya autentikasi:

```bash
ssh -L 8080:127.0.0.1:8080 fleetview@<alamat-pi>
# lalu buka http://localhost:8080 di laptop
```

## B5. Memutakhirkan dan rollback

```bash
cd /opt/fleetview/src && sudo git fetch && sudo git checkout <tag>
sudo ./edge/deploy/scripts/install.sh

sudo /opt/fleetview/deploy/scripts/rollback.sh          # rilis sebelumnya
```

Pemutakhiran dilakukan bertahap, tidak pernah ke seluruh armada sekaligus —
lihat [01 §6](01-deployment.md#6-strategi-pembaruan).

---

# C — Di server central

## C1. Status dan kendali

```bash
sudo systemctl status fleetview-central
sudo systemctl restart fleetview-central
sudo systemctl reload nginx          # setelah mengubah TLS atau rate limit
```

Central boleh mati sebentar. Kapal akan mencoba ulang sendiri dan tidak ada data
yang hilang — itu inti desain offline-first. Yang **tidak** boleh adalah central
berjalan dengan skema basis data yang tidak cocok dengan kodenya.

## C2. Kesehatan

```bash
curl -s https://fleetview.example.com/api/health          # tanpa dependency
curl -s https://fleetview.example.com/api/v1/health \
  -H "Authorization: Bearer <token>" | jq                 # + Postgres & InfluxDB
```

Dua endpoint, dan perbedaannya disengaja — lihat
[03 §7](03-central-deployment.md#7-verifikasi).

## C3. Log

```bash
sudo journalctl -u fleetview-central -f
sudo journalctl -u fleetview-central -o cat | jq 'select(.level=="error")'
sudo tail -f /var/log/nginx/access.log
```

## C4. Administrasi armada

```bash
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin ship list
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin credential list
```

Menambah kapal: [07](07-ship-onboarding.md). Menonaktifkan: [08](08-ship-decommissioning.md).

## C5. Memantau armada

```bash
# Kapal yang tertinggal jauh
curl -s https://fleetview.example.com/api/v1/ships \
  -H "Authorization: Bearer <token>" | jq '[.data[] | select(.pending_records > 100000)]'

# Ruang disk — kegagalan yang menghentikan ingest untuk SELURUH armada
sudo systemctl status fleetview-diskwatch
df -h /var/lib/influxdb
```

## C6. Cadangan

```bash
sudo systemctl start fleetview-backup.service     # jalankan sekarang
sudo systemctl list-timers fleetview-backup.timer # jadwal berikutnya
ls -lh /var/backups/fleetview/
```

Detail dan uji pemulihan: [04](04-backup.md).

## C7. Memutakhirkan

Urutannya penting: **cadangkan, migrasi, baru tukar rilis.**

```bash
sudo systemctl start fleetview-backup.service
cd /opt/fleetview/current/central/api
sudo -u fleetview /opt/fleetview/venv/bin/alembic upgrade head
sudo systemctl restart fleetview-central
```

Catat nomor revisi skema setiap kali deploy. Rollback kode itu aman; rollback
melewati migrasi tidak — lihat [05](05-disaster-recovery.md#skenario-e--rollback-melewati-migrasi).
