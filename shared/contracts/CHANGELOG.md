# Riwayat Versi Schema

Format wire antara Edge Agent dan Central Platform. Setiap batch membawa
`schema_version`, sehingga edge agent versi lama tetap bisa upload selama rollout.

## Aturan perubahan

**MINOR** (mis. 1.0 → 1.1) — backward-compatible. Boleh:
- menambah field opsional
- menambah nilai enum yang punya perilaku default
- melonggarkan validasi

**MAJOR** (mis. 1.x → 2.0) — merusak kompatibilitas. Termasuk:
- menghapus atau mengganti nama field
- membuat field opsional jadi wajib
- memperketat validasi
- mengubah tipe sebuah field

Perubahan MAJOR menuntut central mendukung dua versi sekaligus sampai semua 70
kapal selesai di-upgrade. Kapal bisa offline berminggu-minggu, jadi masa dukung
ganda ini diukur dalam bulan, bukan hari.

## 1.0 — 2026-08-31

Versi pertama. Model: `Reading`, `BatchEnvelope`, `BatchPayload`, `Ack`,
`SyncState`, `UploadSession`, `Heartbeat`, `HeartbeatResponse`, `ExportManifest`.

Dua keputusan yang layak dicatat karena keduanya mahal untuk diubah nanti:

1. **`Reading.fields` berupa map, bukan nilai tunggal.** GPS harus mengirim
   lat/lon/sog/cog sebagai satu titik — kalau dipecah jadi titik terpisah,
   lintang dan bujur bisa tidak konsisten saat salah satunya hilang.
   `Reading.scalar()` disediakan untuk kasus umum bernilai tunggal.

2. **Nilai field boleh `float | int | bool | str`, tidak dikunci ke float.**
   LP-A104 punya 32 input digital (boolean), fungsi counter (integer), dan bisa
   tersambung barcode reader (string). Mengunci ke float akan memaksa perubahan
   MAJOR begitu sinyal semacam itu muncul.

3. **`Reading.source` ada sejak awal.** Membedakan data yang dibaca live dari
   data hasil impor file. Ini konsekuensi langsung dari jalur integrasi LP-A104
   yang belum terkonfirmasi: sebagian kandidat menghasilkan aliran real-time,
   satu kandidat menghasilkan impor file berkala yang wajar membawa timestamp
   lama dan urutan tidak rapi. Lihat `docs/hardware/LP-A104.md`.
