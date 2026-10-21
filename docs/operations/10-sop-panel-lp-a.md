# 10 — SOP: Menyiapkan data panel LP-A agar terbaca FleetView

**Untuk:** tim IoT yang memprogram panel Autonics LP-A (atDesigner dan atLogic).
**Tujuan:** setiap data panel yang ingin dibaca PC atau Raspberry Pi bisa terbaca dengan benar, tanpa tebak-tebakan.
**Dasar teknis:** [LP-A104.md](../hardware/LP-A104.md) dan [konvensi-atlogic.md](../hardware/konvensi-atlogic.md).

---

## Satu aturan yang harus diingat

> PC hanya bisa membaca area **UW/UB** lewat Ethernet. Memori **D dan M tidak terbaca**.
> Setiap data yang mau dibaca PC harus **disalin dulu ke UW dengan `MOV`**.

Panel punya dua "ruangan" memori yang terpisah:

| Ruangan | Isi | Bisa dibaca PC? |
|---|---|---|
| Dalam | D, M, Y, T (tempat PLC bekerja) | Tidak |
| Depan | UW (word), UB (bit) | Ya, lewat CH2 |

Nilai sensor dari CH1 (RS-485) mendarat di area D. Tampilan HMI membaca langsung dari D, jadi layar bisa normal sementara PC melihat nol. Itu bukan kerusakan, hanya belum disalin.

```
sensor ──CH1 RS-485──> D00160
                          │  MOV di ladder atLogic
                          ▼
                       UW01000
                          │  CH2 UNIVERSAL slave, TCP/IP port 502
                          ▼
                    PC / Raspberry Pi
```

Pemetaan alamat: **`UW000nn` = register Modbus `nn`**.

---

## A. Sekali per panel (atDesigner)

Menu: **Project → Project Property → GP/LP Setting → Connected Device Setting**.

| Setelan | Nilai |
|---|---|
| CH1 | Modbus Master, RS-485. **Tidak diubah.** |
| CH2 protokol | `UNIVERSAL` (Vehicle Maker AUTONICS, Series UNIVERSALS) |
| CH2 Communication (Slave) | I/F Setting = `Eth`, **Use or not** tercentang |
| CH2 Type | `TCP/IP` (bukan UDP/IP) |
| CH2 Port | `502` |
| IP panel | tetap, dicatat di inventaris kapal. Contoh: `192.168.100.100`, mask `255.255.255.0` |
| CH3 (bila ada) | Nonaktifkan |

IP juga bisa dilihat atau disetel dari layar panel: sentuh pojok kiri atas, lalu **Environment → Local Ethernet**.

### Waktu mengubah protokol CH2

atDesigner akan membuka dialog **Convert Device Address**. Aturannya:

- Periksa baris yang berawalan `2:` (alamat milik CH2). Baris itu berarti ada objek HMI yang memakai CH2 lama, dan kolom **Converting Device**-nya bisa kosong.
- atDesigner menolak lanjut kalau ada baris Converting yang kosong. **Isi** dulu, jangan asal menekan OK.
- Putuskan per objek: dialihkan ke kanal lain, atau dihapus kalau memang sisa percobaan.

---

## B. Tiap kali menambah data (atLogic)

1. **Catat** nama data dan alamat sumbernya (`D…` atau `M…`).
2. **Pilih alamat tujuan UW yang bebas.** Blok yang dipakai: **UW1000–UW1099**. Pastikan belum ada yang memakainya (cek di tabel inventaris, bagian C).
3. **Tambah baris di akhir program Main, sebelum `END`**, dengan kontak syarat `<SYARAT>` yang ditetapkan tim untuk project ini (lihat catatan di bawah):
   ```
   <SYARAT> ─── MOV  D00160  UW01000     ; RPM
   <SYARAT> ─── MOV  D00108  UW00051     ; F.W Temp
   <SYARAT> ─── MOV  D00110  UW00052     ; L.O Press
   ```
4. **Kirim nilai mentah.** Jangan membagi atau mengali di ladder. Pembagian bilangan bulat membuang presisi tanpa jejak. Penskalaan dilakukan di sisi PC, di satu tempat saja.
5. **Data bit (M)** disalin ke `UB`, atau beberapa bit digabung ke satu word UW.
6. **Batas 123 register** yang dibaca PC dalam satu permintaan. Jangan merancang lebih dari itu dalam satu blok.

> **Menentukan `<SYARAT>`.** Pilih bit yang **selalu ON selama panel beroperasi normal** (penanda "inisialisasi selesai" atau flag "selalu ON" bawaan panel). Jangan memakai bit yang hanya aktif pada kondisi tertentu, karena nilai sensor berhenti tersalin saat bit itu OFF, dan itu terbaca di PC seperti sensor mati.
>
> Di versi project `pla 24 mei 2025`, `MOV H0002 UW00020` bersyarat `M000020` (penanda inisialisasi). Di versi `pla 20 okt 2026`, baris yang sama bersyarat `M000209` (bagian "Monitor"). **Syarat berbeda antar versi**, jadi periksa project yang benar-benar ada di panel dan jangan menyalin `M000020` begitu saja.

---

## C. Wajib diserahkan ke tim PC setiap ada perubahan

Tanpa tabel ini, tim PC harus menebak arti angka.

| Nama data | Sumber (D/M) | UW tujuan | Satuan | Desimal di HMI | Rentang wajar | Catatan |
|---|---|---|---|---|---|---|
| contoh: L.O Press | D00110 | UW00052 | bar | 1 (nilai 31 tampil 3.1) | 0–10 | terverifikasi vs HMI |

Kolom **Desimal di HMI** menentukan skala di sisi PC: 1 desimal = `0.1`, 2 desimal = `0.01`. Cara memastikannya: buka objek Numeric Display data itu di atDesigner dan lihat setelan titik desimalnya.

---

## D. Sebelum dan sesudah download ke panel

**Sebelum:**

- Simpan cadangan project: **Save As** dengan nama baru, atau **Upload** project dari panel. Jangan menimpa file asli.
- Pastikan model di project sama dengan panel fisik. Cek lewat layar panel: menu sistem → **Project → Project Info**, atau pesan di jendela Output atLogic.
- Jangan menyunting atau men-download saat kapal bergantung pada HMI. Download menimpa program yang berjalan.
- Jika atLogic sedang terhubung online ke panel yang berstatus RUN, **jangan** menekan tombol di menu Online atau Debug yang tidak Anda maksud.

**Sesudah:**

- Kabari tim PC supaya mereka mengetes jalur baca.
- Bandingkan angka di PC dengan layar HMI. Kalau berbeda, periksa dulu desimal (skala), baru curigai salinannya.

---

## E. Uji penerimaan (dilakukan bersama tim PC)

1. Dari PC di subnet yang sama: `nc -z -G 3 <IP panel> 502`. Harus menjawab **succeeded**.
2. Baca register yang baru disalin, dan bandingkan dengan HMI.
3. **Opsional tapi murah:** sediakan satu register denyut yang naik terus, misalnya `UW00049`. Kalau nilainya berhenti bertambah sementara sistem lain sehat, yang berhenti adalah blok salin di ladder, bukan mesinnya. Tanpa ini, ladder yang mati terlihat persis seperti mesin yang mati (semua nol).

---

## F. Yang tidak boleh dilakukan

- **Jangan menyadap bus CH1 (RS-485) dari PC.** Dua master pada satu bus tidak diperbolehkan.
- **Jangan meminta lebih dari 123 register sekali baca.** Panel masih membalas, tetapi register terakhir berisi sampah.
- **Jangan menyambung ulang ke panel berkali-kali** saat panel tidak menjawab. Panel bisa menggantung. Biarkan diam sekitar 60 detik agar pulih sendiri.
- **Jangan membaca memori D atau M dari PC** dan menganggap nol itu data. Nol di sana berarti "tidak terekspos".
- **Jangan memakai register D14, D18, D41–D44** sebagai data proses. Itu denyut atau counter program, bukan sensor.

---

## G. Catatan status verifikasi

Poin berikut belum terbukti pada panel yang sekarang dipakai (**LP-A070 T9D6, firmware 2.65**). Aturan dan angkanya diukur pada **LP-A104** (uji 2026-08-21):

| Hal | Status |
|---|---|
| CH2 UNIVERSAL, TCP/IP, port 502 menjawab Modbus TCP | Terbukti di LP-A104. **Belum dites di LP-A070.** |
| `UW000nn` = register Modbus `nn` | Terbukti di LP-A104. Belum dites di LP-A070. |
| Blok bebas UW1000–UW1099 | Autonics mewajibkan MOV ke UW mulai UW1000. Dipetakan dari program LP-A104. **Cek ulang di program panel ini** sebelum dipakai. |
| Bit syarat `M000020` | Ada di versi `pla 24 mei 2025`. Di versi `pla 20 okt 2026` baris `MOV H0002 UW00020` bersyarat `M000209`. **Belum jelas bit mana yang tepat untuk blok salin**, tim yang menentukan. |
| Batas 123 register | Terukur di LP-A104. |
| Pilihan Type di atDesigner hanya `TCP/IP` dan `UDP/IP` | Dilihat langsung di PC ini. Tidak ada opsi bernama "Modbus TCP". |

Setelah satu sensor di LP-A070 terbukti terbaca end-to-end, perbarui tabel ini dan catat hasilnya di [LP-A104.md](../hardware/LP-A104.md).
