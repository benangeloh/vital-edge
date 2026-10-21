/* Penyegaran otomatis untuk Edge Console.
 *
 * Ini SATU-SATUNYA JavaScript di Console, dan halaman sudah lengkap tanpanya.
 * Skrip ini hanya mengganti isi bagian yang menandai dirinya dengan
 * `data-refresh`, supaya teknisi tidak perlu terus menekan muat ulang.
 *
 * Tidak memakai library. Kapal tidak punya internet, jadi tidak ada CDN — dan
 * menyalin blob minified ke repo berarti mengirim kode yang tidak bisa kita
 * baca ke 70 perangkat. Beberapa baris fetch lebih kecil dan bisa diperiksa.
 */
(function () {
  "use strict";

  var MIN_INTERVAL = 2000;

  function refresh(section) {
    var url = section.getAttribute("data-refresh");
    if (!url) return;

    fetch(url, { headers: { "X-Requested-With": "console" } })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.text();
      })
      .then(function (html) {
        // Jangan mengganti isi saat pengguna sedang berinteraksi di dalamnya —
        // fokus dan posisi kursor akan hilang di tengah pekerjaan.
        if (section.contains(document.activeElement)) return;
        section.innerHTML = html;
        section.removeAttribute("data-stale");
      })
      .catch(function () {
        // Kegagalan penyegaran BUKAN alasan mengosongkan halaman. Data lama
        // yang ditandai basi jauh lebih berguna daripada layar kosong — dan
        // agent yang tidak menjawab justru informasi yang dicari teknisi.
        section.setAttribute("data-stale", "true");
      });
  }

  function start() {
    var sections = document.querySelectorAll("[data-refresh]");
    for (var i = 0; i < sections.length; i++) {
      (function (section) {
        var interval = Math.max(
          MIN_INTERVAL,
          parseInt(section.getAttribute("data-interval"), 10) || 10000
        );
        setInterval(function () {
          // Tab yang tersembunyi tidak perlu disegarkan. Pada Pi yang sibuk,
          // permintaan yang tidak akan dilihat siapa pun tetap memakan CPU.
          if (document.hidden) return;
          refresh(section);
        }, interval);
      })(sections[i]);
    }
  }

  // Tombol Cari di formulir sensor: memperbarui hanya area hasil, dengan
  // indikator memuat, tanpa memuat ulang halaman. Tanpa skrip ini tombolnya
  // tetap mengirim formulir seperti biasa.
  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-cari]");
    var box = document.getElementById("hasil-cari");
    if (!btn || !btn.form || !box || btn.disabled) return;
    event.preventDefault();

    var label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Mencari\u2026";
    box.setAttribute("aria-busy", "true");
    box.innerHTML =
      '<p class="busy" role="status"><span class="spinner" aria-hidden="true"></span>' +
      "Mencari di perangkat\u2026</p>";

    fetch(btn.getAttribute("formaction"), {
      method: "POST",
      body: new FormData(btn.form),
      headers: { "X-Requested-With": "console" },
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.text();
      })
      .then(function (html) {
        box.innerHTML = html;
      })
      .catch(function (error) {
        var note = document.createElement("p");
        note.className = "notice notice--err";
        note.setAttribute("role", "alert");
        note.textContent = "Pencarian gagal. " + error.message;
        box.replaceChildren(note);
      })
      .then(function () {
        btn.disabled = false;
        btn.textContent = label;
        box.removeAttribute("aria-busy");
      });
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
