/** Pemformatan untuk tampilan operasional. */

/** Waktu relatif yang ringkas: "2 mnt", "3 jam", "5 hr".
 *
 * Operator membaca kolom ini puluhan kali per menit; bentuk panjang seperti
 * "2 menit yang lalu" membuat kolom melebar dan lebih lambat dipindai. */
export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "Belum ada";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "Belum ada";

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
  if (value === null || value === undefined) return "Tidak ada";
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} rb`;
  return `${(value / 1_000_000).toFixed(1)} jt`;
}

export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return "Belum ada";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Belum ada";
  return date.toLocaleString("id-ID", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function shortId(id: string | null | undefined, length = 8): string {
  return id ? id.slice(0, length) : "Belum ada";
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/** Ukuran berkas dalam satuan 1024, berkoma desimal Indonesia: "1,5 KB", "250 GB". */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "Tidak tersedia";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  if (unit === 0) return `${Math.round(value)} B`;
  const digits = value >= 100 ? 0 : 1;
  return `${value.toFixed(digits).replace(".", ",")} ${BYTE_UNITS[unit]}`;
}

/** Durasi ringkas dua satuan terbesar: "3 jam 5 mnt", "2 hr 4 jam". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return "Tidak tersedia";
  }
  const total = Math.floor(seconds);
  if (total < 60) return `${total} dtk`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} mnt`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rest = minutes % 60;
    return rest === 0 ? `${hours} jam` : `${hours} jam ${rest} mnt`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours === 0 ? `${days} hr` : `${days} hr ${restHours} jam`;
}
