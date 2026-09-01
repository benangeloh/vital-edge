# 08 — Decommissioning Kapal

Menonaktifkan kapal dari armada: kapal dijual, disewakan ke pihak lain, atau
pensiun.

Prinsip yang berlaku sepanjang prosedur ini:

> **Cabut aksesnya, simpan datanya.**

Kredensial harus mati segera. Telemetry-nya tidak dihapus — ia adalah catatan
operasional historis, dan sering dibutuhkan setelah kapal tidak lagi beroperasi:
audit, klaim asuransi, sengketa, atau analisis armada.

---

## 1. Pastikan seluruh data sudah tersetor

**Lakukan ini sebelum apa pun yang lain.** Setelah kredensial dicabut, apa pun
yang tersisa di outbox tidak akan pernah bisa dikirim.

```bash
# Di Pi kapal
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT state, SUM(record_count) FROM outbox GROUP BY state;"
```

Yang berstatus `pending` harus **nol**. Kalau masih ada, biarkan kapal terhubung
sampai selesai, atau ambil lewat export USB:

Export dijalankan dari **Edge Console** di kapal (`http://<pi>:8080/export`),
bukan dari baris perintah. Pilih target USB lalu jalankan. Export **tidak**
menghapus data lokal — ia bukan pengganti ACK, dan outbox tetap `pending` sampai
central benar-benar mengonfirmasi.

Di darat, impor paketnya:

```bash
uv run fleetview-usb-import /media/usb/SHIP-071 \
  --url https://fleetview.example.com --token <token device>

# Verifikasi checksum lebih dulu tanpa mengirim apa pun:
uv run fleetview-usb-import /media/usb/SHIP-071 --url ... --verify-only
```

Verifikasi dari central, bukan dari kapal:

```bash
curl -s https://fleetview.example.com/api/v1/sync/ships/<ship_id> \
  -H "Authorization: Bearer <token>" | jq
```

## 2. Hentikan agent

```bash
sudo systemctl disable --now fleetview-agent fleetview-agent-backup.timer
```

`disable` sekaligus, bukan hanya `stop`. Tanpa itu, agent akan hidup kembali
pada reboot berikutnya dan mencoba mengirim dengan kredensial yang sudah dicabut
— menghasilkan gelombang kegagalan autentikasi di log central yang membingungkan
siapa pun yang menyelidikinya nanti.

## 3. Cabut kredensial perangkat

```bash
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin credential revoke ship-071
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin credential list | grep ship-071
#   -> dicabut  ship-071  ship=...
```

Kredensialnya **ditandai, bukan dihapus**. Audit log dan penelusuran insiden
merujuk padanya; pertanyaan "kredensial mana yang dipakai, dan kapan dicabut"
hanya bisa dijawab kalau barisnya masih ada.

Pencabutan berlaku pada penerbitan token berikutnya. Token yang sudah terbit
tetap berlaku sampai kedaluwarsa (default 1 jam). Kalau kapal berpindah tangan
dan jendela satu jam itu tidak dapat diterima, putar `FLEETVIEW_JWT_SECRET` di
central — tetapi sadari bahwa itu **membatalkan token seluruh armada** dan
memaksa semua kapal melakukan autentikasi ulang.

Untuk kapal yang dijual, biasanya memutar rahasia adalah pilihan yang benar.
Kapal lain akan melakukan autentikasi ulang secara otomatis; yang terjadi hanyalah
jeda sinkronisasi singkat, dan tidak ada data yang hilang.

## 4. Tandai kapal tidak aktif

```bash
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin ship decommission <ship_id>
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin ship list | grep SHIP-071
#   -> nonaktif  SHIP-071  ...
```

Catat alasan dan tanggalnya di inventaris aset. Basis data hanya menyimpan
`is_active`; konteks "dijual ke siapa, kapan" adalah catatan operasional yang
tempatnya di inventaris, bukan di kolom yang tidak akan pernah dibaca kode.

Kapal yang tidak aktif harus keluar dari perhitungan armada dan dari alerting.
Kalau tidak, ia akan selamanya muncul sebagai "offline" dan menambah kebisingan
pada tampilan yang justru harus bisa dipahami dalam sepuluh detik — dan alert
yang terbiasa diabaikan berhenti berfungsi sebagai alert.

**Jangan menghapus baris kapal.** Ledger batch dan telemetry merujuk padanya;
menghapusnya akan memutus riwayat yang masih ingin dibaca.

## 5. Bersihkan perangkat

Kalau perangkat ikut berpindah tangan bersama kapal, **hapus seluruh isinya**.

```bash
sudo shred -u /etc/fleetview/secrets.env
sudo rm -rf /etc/fleetview /opt/fleetview
sudo shred -u /var/lib/fleetview/outbox.db     # hanya SETELAH langkah 1 diverifikasi
```

Untuk SSD, `shred` tidak dapat diandalkan pada penyimpanan flash karena wear
leveling. Pakai penghapusan aman bawaan perangkat, atau hancurkan fisik SSD-nya
kalau kapal berpindah ke pihak yang tidak dipercaya.

Kalau perangkat dibawa kembali, lebih baik dipakai ulang: flash ulang seluruhnya
lalu provisioning sebagai unit baru. Memakai ulang perangkat tanpa flash ulang
berarti membawa serta konfigurasi dan sisa data kapal lama.

## 6. Checklist penutup

- [ ] Outbox kosong dan terverifikasi dari central
- [ ] Agent `disable`, bukan sekadar `stop`
- [ ] Kredensial perangkat dicabut
- [ ] Kapal ditandai `decommissioned`, tidak dihapus
- [ ] Kapal keluar dari alerting dan dari rotasi pemutakhiran
- [ ] Perangkat dihapus aman atau di-flash ulang
- [ ] Inventaris aset diperbarui
- [ ] Kebijakan retensi telemetry untuk kapal ini dicatat — berapa lama disimpan,
      dan siapa yang memutuskan
