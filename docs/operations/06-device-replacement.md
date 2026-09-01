# 06 — Penggantian Perangkat

Mengganti Raspberry Pi yang rusak di sebuah kapal.

Satu keputusan menentukan seluruh sisanya:

> **Apakah SSD lama masih terbaca?**
>
> - **Ya** → data yang belum tersetor bisa diselamatkan seluruhnya. Jalur A.
> - **Tidak** → data yang belum ter-ACK hilang. Jalur B, dan catat kehilangannya.

Yang berharga di perangkat itu bukan perangkatnya, melainkan `outbox.db`: data
yang **belum pernah sampai ke mana pun**. Semua yang sudah ter-ACK sudah aman di
central.

---

## Sebelum berangkat ke kapal

- [ ] Pi pengganti sudah di-provisioning penuh dan **sudah diuji** — seluruh
      langkah [02](02-provisioning-raspberry-pi.md#7-verifikasi-sebelum-dikirim),
      termasuk uji cabut daya
- [ ] Versi agent-nya **sama** dengan armada, bukan yang terbaru. Kapal bukan
      tempat untuk sekaligus menguji rilis baru
- [ ] `ship_id` dan `device_id` kapal tujuan sudah dicatat
- [ ] Kredensial perangkat **baru** sudah diterbitkan di central
- [ ] Enclosure SSD USB, untuk membaca SSD lama
- [ ] Foto pemasangan kabel serial dari catatan provisioning

Kredensial harus baru. Menyalin `secrets.env` dari perangkat rusak berarti dua
perangkat memegang kredensial yang sama, dan pencabutan kredensial jadi tidak bisa
menyasar satu perangkat saja.

---

## Jalur A — SSD lama masih terbaca

```bash
# 1. Di Pi lama, kalau masih bisa dinyalakan: hentikan dengan rapi.
sudo systemctl stop fleetview-agent
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT state, SUM(record_count) FROM outbox GROUP BY state;"   # catat angkanya
```

```bash
# 2. Pindahkan SSD ke Pi pengganti. Kalau bisa, pakai SSD lamanya langsung —
#    itu memindahkan seluruh outbox tanpa menyalin apa pun.
sudo mount /dev/sda1 /var/lib/fleetview
sudo sqlite3 /var/lib/fleetview/outbox.db "PRAGMA integrity_check;"
sudo chown -R fleetview:fleetview /var/lib/fleetview
```

```bash
# 3. Pasang identitas kapal + kredensial BARU.
sudo nano /etc/fleetview/edge.yaml       # ship_id, ship_name, device_id
sudo nano /etc/fleetview/secrets.env     # client_id + secret baru
```

**`device_id` berubah, `ship_id` tidak.** Kapalnya sama; perangkatnya berbeda.
Menyamakan `device_id` akan membuat riwayat dua perangkat fisik tercampur menjadi
satu, dan penelusuran kegagalan perangkat keras menjadi mustahil.

```bash
# 4. Nyalakan dan pastikan backlog benar-benar tersetor.
sudo systemctl start fleetview-agent
watch -n 10 'sudo sqlite3 /var/lib/fleetview/outbox.db \
  "SELECT COALESCE(SUM(record_count),0) FROM outbox WHERE state=\"pending\";"'
```

Angka pending harus **turun**. Kalau tidak bergerak, lihat
[09 — Troubleshooting](09-troubleshooting.md) bagian sinkronisasi.

### Kalau SSD lama tidak bisa dipakai langsung

Salin outbox-nya ke perangkat baru sebelum agent dijalankan pertama kali:

```bash
sudo systemctl stop fleetview-agent
sudo sqlite3 /path/ke/ssd-lama/outbox.db ".backup '/var/lib/fleetview/outbox.db'"
sudo chown fleetview:fleetview /var/lib/fleetview/outbox.db
sudo systemctl start fleetview-agent
```

Ini harus dilakukan **sebelum** agent baru sempat mengalokasikan sequence
sendiri. Kalau agent sudah sempat berjalan, outbox barunya sudah punya counter
yang dimulai dari nol, dan menimpanya dengan outbox lama akan membuat sequence
mundur. Periksa dan perbaiki seperti pada
[05 Skenario D](05-disaster-recovery.md#skenario-d--outbox-rusak).

---

## Jalur B — SSD lama tidak terbaca

Data yang belum ter-ACK hilang. Itu tidak bisa diperbaiki; yang bisa dilakukan
adalah mencatatnya dengan jujur supaya celah datanya diketahui, bukan ditemukan
berbulan-bulan kemudian oleh orang yang menganalisis.

```bash
# 1. Cari tahu batas kehilangannya dari central.
curl -s https://fleetview.example.com/api/v1/sync/ships/<ship_id> \
  -H "Authorization: Bearer <token>" | jq
```

`highest_sequence_seen` dan `last_batch_received_at` menandai batas terakhir data
yang selamat. Segala sesuatu setelah itu hilang.

```bash
# 2. Pasang Pi pengganti seperti provisioning normal, dengan outbox KOSONG.
# 3. Naikkan counter sequence di atas yang terakhir diterima central.
sudo systemctl stop fleetview-agent
sudo sqlite3 /var/lib/fleetview/outbox.db \
  "INSERT INTO counters(name,value) VALUES('sequence', <highest_sequence_seen + 1000>)
   ON CONFLICT(name) DO UPDATE SET value = excluded.value;"
sudo systemctl start fleetview-agent
```

Langkah 3 wajib. Perangkat baru memulai sequence dari nol, dan tanpa penyesuaian
ini ia akan memakai ulang nomor yang sudah dipakai perangkat lama — sehingga
central menimpa data lama dengan data baru. Marjin 1000 memberi jarak aman
terhadap ketidakpastian batch yang sedang dalam perjalanan.

**4. Catat celah datanya di inventaris aset dan catatan operasional kapal**,
dengan rentang waktunya: dari `last_batch_received_at` sampai saat perangkat baru
mulai mengirim.

Catatan ini penting justru karena tidak ada di sistem. Celah data yang tidak
tercatat akan ditemukan berbulan-bulan kemudian oleh orang yang menganalisis
tren, dan tanpa penjelasan ia akan tampak seperti anomali operasional kapal.

---

## Setelah selesai

- [ ] Cabut kredensial perangkat lama di central — ini yang paling mudah terlupa,
      dan meninggalkannya aktif berarti kredensial yang beredar di perangkat yang
      tidak lagi kita kuasai:
      ```bash
      sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin credential revoke <client_id lama>
      ```
- [ ] Verifikasi kapal muncul sehat di dashboard, dan `pending` menurun
- [ ] Perbarui inventaris aset: serial perangkat baru, tanggal, `device_id`
- [ ] Bawa turun perangkat lama. **Hapus SSD-nya secara aman** sebelum dibuang
      atau diperbaiki — di dalamnya ada kredensial dan telemetry operasional
- [ ] Kalau SSD masih terbaca, simpan citranya sampai dipastikan seluruh backlog
      sudah tersetor
