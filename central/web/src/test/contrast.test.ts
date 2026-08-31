import { describe, expect, it } from "vitest";

/** Rasio kontras WCAG untuk pasangan warna yang benar-benar dipakai.
 *
 * axe-core memeriksa kontras lewat canvas, dan canvas tidak ada di jsdom —
 * artinya pemeriksaan kontras di test a11y **tidak benar-benar berjalan**.
 * Daripada mengira sudah teruji, rasionya dihitung langsung di sini.
 *
 * Dashboard ini dipakai berjam-jam, kadang di layar anjungan yang terkena
 * matahari. Kontras bukan formalitas di sini. */

function luminance(hex: string): number {
  const value = hex.replace("#", "");
  const channels = [0, 2, 4].map((i) => {
    const c = parseInt(value.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function ratio(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light! + 0.05) / (dark! + 0.05);
}

const WHITE = "#ffffff";
const SURFACE = "#f7f8f7";
const TEXT = "#12211c";
const TEXT_MUTED = "#5c6b64";
const GREEN_DEEP = "#012b24";
const GREEN_BRIGHT = "#0fbc58";

describe("kontras teks utama (WCAG AA: 4.5:1)", () => {
  it.each([
    ["teks di atas putih", TEXT, WHITE],
    ["teks di atas surface", TEXT, SURFACE],
    ["teks redup di atas putih", TEXT_MUTED, WHITE],
    ["teks redup di atas surface", TEXT_MUTED, SURFACE],
    ["teks putih di atas hijau pekat (sidebar)", WHITE, GREEN_DEEP],
  ])("%s", (_name, fg, bg) => {
    expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("kontras teks lencana status (WCAG AA)", () => {
  it.each([
    ["ok", "#0a6b34", "#e8f8ee"],
    ["sync", "#14507f", "#e9f2fa"],
    ["warn", "#8a5300", "#fdf3e3"],
    ["crit", "#99091f", "#fdeaed"],
    ["idle", TEXT_MUTED, "#f0f2f1"],
  ])("lencana %s", (_name, fg, bg) => {
    expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("kontras tombol aksi utama", () => {
  it("hijau terang dengan teks hijau pekat memenuhi AA", () => {
    // Kombinasi ini dipilih justru karena putih di atas hijau terang TIDAK
    // memenuhi AA — teks gelap di atas hijau terang memenuhinya dengan lega.
    expect(ratio(GREEN_DEEP, GREEN_BRIGHT)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("kontras elemen non-teks (WCAG 1.4.11: 3:1)", () => {
  /* Indikator kecil memakai token --mark-*, bukan warna brand langsung.
     Hijau terang #0fbc58 hanya mencapai 2,5:1 di atas putih; sebuah titik
     status yang tidak terlihat bukan status. */
  it.each([
    ["titik status ok", "#2a7a44"],
    ["titik status peringatan", "#c77700"],
    ["titik status kritis", "#ce0f2d"],
    ["titik status idle", "#7d8d85"],
    ["titik status sinkron", "#1f6fb2"],
  ])("%s di atas putih", (_name, color) => {
    expect(ratio(color, WHITE)).toBeGreaterThanOrEqual(3);
  });

  it("hijau brand memang tidak memenuhi syarat sebagai indikator kecil", () => {
    // Ditulis eksplisit supaya tidak ada yang mengembalikannya tanpa sengaja.
    expect(ratio("#0fbc58", WHITE)).toBeLessThan(3);
  });
});
