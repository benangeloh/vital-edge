/** Pemformatan untuk tampilan operasional. */

/** Waktu relatif yang ringkas: "2 mnt", "3 jam", "5 hr".
 *
 * Operator membaca kolom ini puluhan kali per menit; bentuk panjang seperti
 * "2 menit yang lalu" membuat kolom melebar dan lebih lambat dipindai. */
export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";

  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return `${seconds} dtk`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} mnt`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} jam`;
  return `${Math.round(hours / 24)} hr`;
}

/** Seberapa gawat sebuah data yang tertinggal. Menentukan warna kolom. */
export function staleness(iso: string | null | undefined, now = Date.now()): "fresh" | "stale" | "old" | "none" {
  if (!iso) return "none";
  const age = (now - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(age)) return "none";
  if (age < 300) return "fresh";
  if (age < 3600) return "stale";
  return "old";
}

export function compactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} rb`;
  return `${(value / 1_000_000).toFixed(1)} jt`;
}

export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("id-ID", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function shortId(id: string | null | undefined, length = 8): string {
  return id ? id.slice(0, length) : "—";
}
