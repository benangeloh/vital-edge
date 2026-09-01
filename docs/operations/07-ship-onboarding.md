# 07 — Onboarding Kapal Baru

**Dokumen ini alur lengkapnya**, dari kapal yang belum terdaftar sampai
datanya terlihat di dashboard. Kalau bingung harus mulai dari mana, mulai dari
sini.

---

## Peta alur

Empat tahap. Tiga dikerjakan **di darat**, dan itu disengaja: kapal bukan tempat
untuk instalasi pertama.

```
TAHAP 1                TAHAP 2                TAHAP 3            TAHAP 4
Daftarkan kapal   →    Siapkan Pi        →    Pasang di kapal →  Verifikasi
di dashboard           di meja kerja                             dari darat

di darat               di darat               DI KAPAL           di darat
admin/operator         teknisi                teknisi            admin/operator
~5 menit               ~45 menit              ~2 jam             ~10 menit

menghasilkan:          menghasilkan:          menghasilkan:      menghasilkan:
ship_id                Pi siap kirim,         Pi hidup dan       kapal resmi
device_id              sudah diuji            tersambung         masuk armada
client_id + secret     cabut daya             LP-A104
```

**Kredensial adalah benang merahnya.** Tahap 1 menerbitkannya di central, tahap
2 memasukkannya ke Pi, dan tahap 3–4 membuktikan keduanya benar-benar cocok.
Tidak ada auto-discovery: Pi tidak bisa mendaftarkan dirinya sendiri, karena
kalau bisa, siapa pun yang menyalakan Pi di jaringan bisa mengirim data atas
nama kapal mana pun.

Setiap tahap punya **gerbang** — satu hal yang harus terbukti sebelum lanjut.
Melewatinya berarti menemukan masalah di kapal, di tengah laut, alih-alih di
meja kerja.

---

## TAHAP 1 — Daftarkan kapal di central

*Di darat · dashboard · ~5 menit*

Provisioning butuh `ship_id`, `device_id`, dan kredensial yang hanya bisa
diterbitkan central. Jadi ini selalu duluan.

1. Buka halaman **Kapal**, tekan **Tambah kapal**
2. Isi **nama kapal** dan **kode kapal** (mis. `SHIP-071`). IMO dan nama
   perangkat opsional.
3. Tekan **Daftarkan kapal**

Satu langkah itu membuat kapal, perangkat Edge, dan kredensialnya sekaligus
dalam satu transaksi. Kalau dipecah dan gagal di tengah, akan tertinggal kapal
tanpa perangkat — keadaan yang harus dibereskan lewat SSH ke server.

Layar berikutnya menampilkan dua potongan siap salin. **Simpan keduanya
sekarang**; keduanya dibutuhkan di Tahap 2.

```
1. /etc/fleetview/secrets.env          ← RAHASIA
   FLEETVIEW_SYNC__DEVICE_CLIENT_ID=ship-071
   FLEETVIEW_SYNC__DEVICE_SECRET=<rahasia panjang>

2. /etc/fleetview/edge.yaml            ← identitas, bukan rahasia
   ship:
     ship_id: "..."
     ship_name: "KM Sinar Jaya"
     device_id: "..."
```

**Rahasianya hanya ditampilkan sekali.** Di basis data ia hanya ada sebagai hash
Argon2id — tidak ada cara membacanya kembali. Kalau hilang, terbitkan yang baru
lalu cabut yang lama. Jangan mengirimkannya lewat email, chat, atau tiket; kalau
harus lewat perantara, pakai penyimpan rahasia yang sudah dipakai tim.

**Kode kapal tidak bisa diubah** setelah kapal beroperasi: ia menjadi nama
direktori paket ekspor USB, dan paket lama akan berhenti cocok. Pakai format
konsisten (`SHIP-NNN`).

> ### ✓ Gerbang Tahap 1
> Kapal muncul di halaman **Kapal** dengan status `Offline` — wajar, Pi-nya
> memang belum ada. Kedua potongan sudah tersimpan di tempat yang aman.

<details>
<summary>Alternatif: lewat baris perintah</summary>

Untuk mendaftarkan banyak kapal sekaligus, atau saat dashboard belum tersedia:

```bash
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin ship add \
  --name "KM Sinar Jaya" --slug SHIP-071 --imo 9123456

sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin device add \
  --ship-id <ship_id> --name "Edge Pi #1" --hardware "Raspberry Pi 4B"

sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin credential issue \
  --device-id <device_id> --client-id ship-071
```
</details>

---

## TAHAP 2 — Siapkan Raspberry Pi

*Di darat, di meja kerja · teknisi · ~45 menit, sebagian besar menunggu*

Langkah teknisnya panjang dan ada di
**[02 — Provisioning Raspberry Pi](02-provisioning-raspberry-pi.md)**. Jangan
kerjakan di kapal: tidak ada internet yang bisa diandalkan, dan kesalahan kecil
berarti perangkat harus dibawa turun lagi.

Ringkasnya, dokumen itu memandu:

| § | Isi | Kenapa penting |
|---|---|---|
| 1–3 | OS 64-bit, SSD, zona waktu | SD card habis dalam hitungan bulan; UTC dianjurkan agar log 70 kapal bisa disandingkan |
| 4 | InfluxDB lokal, retensi 90 hari | Tanpa batas, disk penuh dan akuisisi berhenti diam-diam |
| 5 | Pasang Edge Agent | |
| 6 | **Masukkan kredensial Tahap 1** | Di sinilah kedua potongan tadi dipakai |
| 7 | Verifikasi, termasuk **uji cabut daya** | |

Selain itu, siapkan **`/etc/fleetview/sensors.yaml`** — pemetaan sensor kapal
ini. Isinya berbeda antar kapal, bahkan antar kapal sekelas:

- `sensor_id` harus **stabil selamanya**. Ia menjadi tag di InfluxDB, jadi
  mengubahnya menciptakan deret waktu baru dan memutus riwayat.
- Jangan memasukkan nilai yang berubah-ubah sebagai tag. Kardinalitas tinggi
  adalah cara tercepat membuat InfluxDB tidak bisa dipakai.
- `unit` harus benar sejak awal. Mengubahnya belakangan membuat riwayat kapal
  bercampur antara dua satuan tanpa penanda.

> ### ✓ Gerbang Tahap 2
> Seluruh Verifikasi §7 lolos, termasuk **uji cabut daya**: cabut listrik
> secara fisik, lalu pastikan jumlah record di outbox **tidak berkurang** dan
> `PRAGMA integrity_check` menjawab `ok`.
>
> Uji itu sering dilewati karena merepotkan. Justru itu yang memverifikasi janji
> utama sistem ini — dan mencabut kabel di meja kerja jauh lebih murah daripada
> menemukan masalahnya saat kapal sudah di laut.

---

## TAHAP 3 — Pasang di kapal

*Di kapal · teknisi · ~2 jam*

- [ ] Pi di lokasi kering, berventilasi, tidak kena getaran langsung
- [ ] Daya lewat **UPS/DC-UPS**, bukan langsung ke bus kapal
- [ ] Kabel serial ke LP-A104 terpasang dan terikat rapi
- [ ] Terhubung ke jaringan kapal, kalau ada
- [ ] Difoto untuk rujukan teknisi berikutnya

Sebelum turun dari kapal, jalankan satu perintah:

```bash
fleetview-status
```

Ia memeriksa layanan, apakah polling benar-benar **maju**, data yang belum
tersetor, ruang disk, dan kesalahan terakhir. Lihat
[00 §B1b](00-menjalankan.md#b1b-satu-perintah-untuk-melihat-semuanya).

> ### ✓ Gerbang Tahap 3
> `fleetview-status` menunjukkan **polling maju** (angka bertambah antara dua
> cuplikan) dan jumlah record di outbox bertambah.
>
> Kalau kapal punya konektivitas, `pending` juga harus **turun** — itu tanda
> kredensial Tahap 1 benar-benar cocok. Kalau kapal masih di dermaga tanpa
> jaringan, `pending` yang menumpuk adalah hal normal; ia akan tersetor sendiri
> begitu ada koneksi.

---

## TAHAP 4 — Verifikasi dari darat

*Di darat · admin/operator · ~10 menit*

Gerbang terakhir. **Kapal belum dianggap onboarded sampai keempatnya lolos.**

```bash
# 1. Data benar-benar sampai
curl -s https://fleetview.example.com/api/v1/sync/ships/<ship_id> \
  -H "Authorization: Bearer <token>" | jq
#    last_batch_received_at terisi dan baru
#    last_contiguous_sequence == highest_sequence_seen  (tidak ada celah)

# 2. Kapal muncul di dashboard dengan status sehat

# 3. Telemetry bisa DIBACA KEMBALI, bukan sekadar diterima
curl -s "https://fleetview.example.com/api/v1/telemetry/latest?ship_id=<ship_id>" \
  -H "Authorization: Bearer <token>" | jq
```

**4. Nilainya masuk akal.** Ini tidak bisa diotomatiskan dan paling sering
dilewati. Bandingkan pembacaan RPM, suhu, dan tekanan dengan panel di ruang
mesin. Sensor yang salah dipetakan menghasilkan angka yang tampak sempurna di
dashboard dan sepenuhnya keliru — kegagalan paling mahal, karena baru ketahuan
setelah dipakai mengambil keputusan.

> ### ✓ Gerbang Tahap 4 — selesai
> Keempatnya lolos. Kapal resmi masuk armada.

---

## Setelah onboarding

- [ ] Inventaris aset diperbarui: kapal, `ship_id`, `device_id`, serial
      perangkat, tanggal, versi agent, `config_version`
- [ ] `client_id` dicatat; **`secret` tidak dicatat di mana pun** selain di Pi
- [ ] Amati 72 jam pertama: perhatikan `pending` yang menumpuk dan
      `polls_failed` yang naik
- [ ] Masukkan kapal ke rotasi pemutakhiran armada
      ([01 §6](01-deployment.md#6-strategi-pembaruan))

---

## Kalau ada yang tidak beres

| Gejala | Kemungkinan | Lihat |
|---|---|---|
| Agent gagal start | Config tidak lengkap, outbox rusak | [09](09-troubleshooting.md#agent-gagal-start-berulang) |
| `polls` tidak bertambah | Kabel serial, perangkat mati | [09](09-troubleshooting.md#agent-hidup-tetapi-tidak-ada-data-terkumpul) |
| `pending` naik terus | Kredensial salah/dicabut, tidak ada koneksi | [09](09-troubleshooting.md#pending-terus-bertambah-dan-tidak-pernah-turun) |
| Data sampai, nilainya aneh | Pemetaan sensor salah | Periksa `sensors.yaml` terhadap panel ruang mesin |
| Rahasia hilang sebelum sempat disalin | — | Terbitkan ulang; lihat di bawah |

### Menerbitkan ulang kredensial

Saat Raspberry Pi diganti, atau saat rahasia hilang — dari halaman **Perangkat**
di dashboard, atau:

```bash
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin credential issue \
  --device-id <device_id> --client-id <baru>
sudo -u fleetview /opt/fleetview/venv/bin/fleetview-admin credential revoke <lama>
```

Kredensial lama **tidak** otomatis dicabut saat menerbitkan yang baru. Itu
disengaja: perangkat lama mungkin masih menyetor sisa backlog-nya, dan
mencabutnya seketika akan mengunci data itu di kapal. Cabut setelah backlog habis.

### Catatan LP-A104

Adapter `lp_a104` **belum bisa dipakai di produksi** — jalur baca dari perangkat
belum terkonfirmasi, dan adapter-nya sengaja gagal keras dengan pesan yang
menjelaskan. Lihat [docs/hardware/LP-A104.md](../hardware/LP-A104.md).

Sampai itu dikonfirmasi bersama vendor, Tahap 1–2 bisa diselesaikan penuh
tetapi Tahap 3–4 belum bisa. **Jangan** menyiasatinya dengan adapter
`simulator`: kapal yang mengirim data simulasi akan tampak sehat sempurna di
dashboard dan tidak akan ada yang menyadarinya.
