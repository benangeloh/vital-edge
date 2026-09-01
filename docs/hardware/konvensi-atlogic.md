# Konvensi atLogic / atDesigner untuk LP-A104

Supaya panel dan FleetView selalu cocok tanpa mengubah kode — cukup menyesuaikan
di atLogic dan atDesigner.

Dasarnya adalah pengujian lapangan 2026-08-21; lihat
[LP-A104.md](LP-A104.md) untuk buktinya.

---

## Yang harus dipahami lebih dulu

Nilai sensor masuk lewat **CH1 (RS-485)** dan mendarat di area **`D`** milik PLC.
Yang diekspos ke Ethernet adalah area **`UW`**. Keduanya **memori yang berbeda**
meski penamaannya mirip — inilah sumber semua kebingungan soal D108/D110/D160.

```
sensor ──CH1 RS-485──> D00160 (area D, PLC)
                          │
                          │  MOV  ← satu baris di ladder, WAJIB
                          ▼
                       UW00050 (area UW)
                          │
                          │  CH2 Modbus TCP Slave, port 502
                          ▼
                    register Modbus 50 ──> FleetView
```

Pemetaannya: **`UW000nn` ↔ register Modbus `nn`**.

Tanpa baris `MOV`, sensor tidak akan pernah terbaca dari luar — dan membaca
register 108/110/160 selalu mengembalikan nol, karena yang ada di sana adalah
`UW00108` dst. yang memang kosong.

---

## 1. atDesigner — sekali saja per kapal

| Setelan | Nilai | Kenapa |
|---|---|---|
| CH2 `UNIVERSAL (AUTONICS)` | **Modbus TCP Slave**, port **502** | inilah kanal yang dibaca FleetView |
| IP panel | tetap, dicatat di inventaris | dimasukkan ke `collector.lp_a104_host` |
| CH1 | Modbus Master RS-485 | jalur sensor, tidak diubah |
| CH3 `ModbusMaster_02` | **nonaktifkan** | lihat catatan di bawah |

**Nonaktifkan CH3.** Diuji di lapangan: arahnya terbalik untuk keperluan ini —
panel mengirim FC04 (permintaan **baca**) ke PC, bukan menulis data. Dan ia tetap
menembak endpoint tiap 500 ms meski tidak ada yang mendengar.

---

## 2. atLogic — tiap kali menambah sensor

### Alamat tujuan: pakai UW45–UW80

Dipetakan langsung dari panel. Yang **sudah terpakai** panel: D0, D7–D14, D18,
D20, D33, D34, D39, D41–D44, D81. Blok bebas terbesar adalah **UW45–UW80**.

Di atas UW81 memang kosong, tetapi belum pernah diverifikasi bisa dibaca lewat
Modbus pada panel ini. Kalau butuh lebih dari 36 titik, uji satu alamat di atas
UW100 lebih dulu dengan angka tetap sebelum memakainya untuk sensor.

### Bentuk baris salin

Edge Console **menghasilkan blok ini otomatis** — halaman **Sensor**, kotak
"Baris untuk ditempel ke atLogic". Salin ke akhir program Main, tepat sebelum
`END`, lalu download.

```
LOAD  M000020
MOV   D00160   UW00050      ; me_port_rpm
LOAD  M000020
MOV   D00108   UW00051      ; fw_temp
LOAD  M000020
MOV   D00110   UW00052      ; lo_press
```

`M000020` dipakai karena rung 26 dan 32 sudah memakainya sebagai penanda
"inisialisasi selesai" — blok salin ikut berjalan terus selama panel beroperasi,
tanpa menambah kontak baru yang harus ikut dipelihara.

### Jangan menskalakan di ladder

Kirim nilai **mentah** apa adanya. Penskalaan dilakukan di FleetView lewat kolom
`scale` di halaman Sensor.

Alasannya: pembagian di ladder memakai bilangan bulat, sehingga presisinya hilang
tanpa jejak — dan kalau kedua sisi sama-sama menskalakan, hasilnya salah tanpa
gejala apa pun. Satu tempat saja yang menskalakan, dan tempat itu adalah yang
bisa diperbaiki tanpa mendatangi kapal.

---

## 3. Menyamakan skala dengan HMI

Register Modbus hanya memuat bilangan bulat 16-bit; koma tidak ada di protokolnya.
Objek Numeric Display di HMI yang memasang titik desimalnya.

Cara memastikan, sekali per sensor:

1. Buka objek Numeric Display sensor itu di atDesigner
2. Lihat setelan titik desimalnya
3. Isi `scale` di halaman Sensor: 1 desimal → `0.1`, 2 desimal → `0.01`

| Sensor | Mentah | HMI | `scale` | Status |
|---|---|---|---|---|
| `lo_press` | 31 | 3.1 bar | `0.1` | **terverifikasi** |
| `rpm` | — | — | `1` | asumsi, **perlu dicocokkan saat mesin jalan** |
| `fw_temp` | — | — | `1` | asumsi, **perlu dicocokkan saat mesin jalan** |

---

## 4. Urutan kerja menambah satu sensor

1. **Di dashboard** (halaman Sensor): isi nama, alamat `UW5x`, alamat sumber
   `D00xxx`, besaran, satuan, skala, batas wajar
2. **Salin blok ladder** yang muncul di halaman itu
3. **Tempel ke atLogic**, sebelum `END`, lalu download ke panel
4. **Restart agent**: `sudo systemctl restart fleetview-agent`
5. **Periksa**: `sudo fleetview-status`, lalu bandingkan angkanya dengan layar HMI

Tidak ada langkah yang menuntut menyunting berkas atau menulis kode.

---

## 5. Laju data

Panel membaca sensor lewat CH1 dengan Group Interval 5 dan Time Out 1000 ms.
Nilai di area `UW` diperbarui mengikuti laju itu, **bukan** laju polling
FleetView. Menyetel `poll_interval_seconds` lebih cepat dari itu tidak
menghasilkan data baru — hanya menambah beban panel.

Mulailah dari 1 detik. Turunkan hanya kalau memang terbukti tertinggal.

---

## 6. Yang tidak boleh dilakukan

**Jangan meminta lebih dari 123 register sekali baca.** Ini bukan batas Modbus
(yang 125), melainkan batas terukur panel ini: 124 membuat satu register terakhir
berisi sampah, 125 membuat dua terakhir sampah — dan **panel tetap membalas
dengan byte-count yang tampak benar**, jadi kerusakannya lolos sebagai data yang
wajar. FleetView sudah menegakkan batas ini.

**Jangan membuka koneksi baru tiap pembacaan.** Panel menahan sesi yang sudah
ditutup di `FIN_WAIT_2` sampai slotnya habis, lalu berhenti menjawab Modbus sama
sekali meski port 502 tetap menerima koneksi.

**Kalau panel berhenti membalas, biarkan diam ~60 detik.** Ia pulih sendiri —
tetapi hanya kalau benar-benar tidak diganggu. Menyambung ulang terus-menerus
justru menahannya tergantung.

---

## 7. Memastikan blok salin benar-benar jalan

Kegagalan yang paling sulit dilihat: ladder berhenti menyalin, dan semua sensor
membaca nol — persis seperti mesin yang mati.

Cara membedakannya: sediakan satu register **denyut** yang naik terus, misalnya
`UW00049`, lalu daftarkan sebagai sensor di dashboard. Kalau nilainya berhenti
bertambah sementara agent tetap sehat, yang berhenti adalah blok salinnya —
bukan mesinnya.

Ini opsional, tetapi murah dan menjawab pertanyaan yang kalau tidak akan
memerlukan kunjungan ke kapal.
