# 07 — Arah Desain UI

Berlaku untuk Central Dashboard (Phase 6) dan, dalam bentuk lebih sederhana, Edge Console
(Phase 7).

## Arah

**Console operasional bergaya Swiss/International** — bukan template admin. Acuannya adalah
ruang kendali lalu lintas kapal atau layar dinding NOC: padat di tempat yang memang perlu
padat, tenang di tempat lainnya, dengan antarmuka yang mundur ke belakang supaya kondisi
armada terbaca dalam sekali lihat.

Konkretnya: grid yang ketat, whitespace yang lega, dekorasi mendekati nol, dan hierarki yang
dibawa oleh **skala tipografi dan kepadatan**, bukan oleh warna atau garis border. Warna
hampir sepenuhnya dicadangkan untuk menyatakan status.

## Token

```css
:root {
  /* Brand — dari referensi SPIL */
  --green-deep:    #012B24;   /* header, nav, permukaan paling gelap */
  --green-dark:    #004430;   /* permukaan gelap sekunder */
  --green-primary: #34854C;   /* brand, link, item terpilih */
  --green-bright:  #0FBC58;   /* aksi utama, status sehat */
  --red-brand:     #CE0F2D;   /* aksen brand, kondisi kritis, aksi destruktif */

  /* Netral — ini yang sebenarnya bekerja paling keras */
  --white:         #FFFFFF;
  --surface:       #F7F8F7;
  --border:        #E4E8E5;
  --text:          #12211C;
  --text-muted:    #5C6B64;

  /* Status semantik — satu-satunya tempat warna boleh "berbicara" */
  --status-ok:     var(--green-bright);
  --status-warn:   #C77700;   /* amber: tetap bisa dibedakan dari merah pada deuteranopia */
  --status-crit:   var(--red-brand);
  --status-idle:   #94A39C;   /* offline / tidak ada data — abu-abu, bukan merah */
}
```

## Aturan

1. **Hijau bukan warna halamannya.** Latar halaman adalah putih dan `--surface`. Hijau muncul
   di shell navigasi, tombol aksi utama, dan indikator status sehat. Card berwarna putih
   dengan border 1px `--border`, tidak pernah bersemu hijau.
2. **Merah berarti "bertindak sekarang."** Alert kritis, konfirmasi destruktif, tanda brand.
   Tidak pernah untuk "offline" — kapal yang offline itu wajar (justru itu premis seluruh
   sistem ini) dan mendapat abu-abu `--status-idle`.
3. **Status tidak pernah hanya mengandalkan warna.** Setiap indikator memasangkan warna dengan
   bentuk atau simbol (● online / ◐ degraded / ○ offline / ▲ alert), supaya tetap terbaca oleh
   pengguna buta warna dan tetap jelas di layar anjungan yang terkena sinar matahari.
4. **Satu aksen per tampilan.** Kalau semuanya ditonjolkan, tidak ada yang menonjol.
5. **Tipografi:** satu keluarga font (Inter atau IBM Plex Sans) dengan angka tabular untuk
   semua nilai numerik — kolom telemetry tidak boleh bergoyang saat angkanya berubah. Hierarki
   memakai skala rasio 1,25; font monospace hanya untuk ID, sequence, dan keluaran log.
6. **Chart** (mengikuti skill `dataviz`): tanpa gridline berlebihan, tanpa hiasan yang tidak
   membawa informasi, pelabelan langsung ketimbang legenda bila ruangnya cukup, warna seri
   yang konsisten per sensor di semua tampilan. Sumbu Y dimulai dari nol hanya bila nol
   memang bermakna.
7. **Kepadatan adalah pengaturan.** Fleet overview menyediakan mode comfortable/compact —
   operator yang mengawasi 70 kapal ingin compact, pengunjung sesekali tidak.
8. **Animasi hanya yang fungsional** — perpindahan status dan pembaruan nilai, 150–200 ms,
   hanya `transform`/`opacity`, dan sepenuhnya dimatikan saat `prefers-reduced-motion`.

## Yang sengaja dihindari

Tidak ada hero bergradasi. Tidak ada grid card berjarak seragam dengan penekanan yang sama
rata. Tidak ada KPI tile bersemu hijau. Tidak ada ikon dekoratif. Tidak ada apa pun yang
terasa pas di dalam starter shadcn generik — ini harus terbaca sebagai instrumen, bukan
sebagai website.
