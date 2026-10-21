import type { ReadingRow } from "./types";

/** Rentang tanggal, ekspor CSV, dan hal kecil lain untuk tampilan logger. */

export interface RangePreset {
  id: "1h" | "6h" | "24h" | "7d" | "30d";
  label: string;
  seconds: number;
}

export const PRESETS: RangePreset[] = [
  { id: "1h", label: "1 jam", seconds: 3600 },
  { id: "6h", label: "6 jam", seconds: 6 * 3600 },
  { id: "24h", label: "24 jam", seconds: 24 * 3600 },
  { id: "7d", label: "7 hari", seconds: 7 * 86_400 },
  { id: "30d", label: "30 hari", seconds: 30 * 86_400 },
];

/** Batas yang sama dengan API. Dicek di sini juga supaya pesannya muncul di
 *  samping isian, bukan sebagai galat setelah tombol ditekan. */
export const MAX_RANGE_DAYS = 400;

/** Batas baris per unduhan CSV. Lebih dari ini lebih baik dipecah per rentang. */
export const EXPORT_MAX_ROWS = 5000;

/** Rentang yang sedang dilihat. Bila `presetId` terisi, ujung akhirnya selalu
 *  "sekarang" setiap kali data dimuat, sehingga tabel terus mengikuti data baru. */
export interface LoggerRange {
  presetId: RangePreset["id"] | null;
  start: Date;
  stop: Date;
}

export function resolveRange(range: LoggerRange, now = new Date()): { start: Date; stop: Date } {
  const preset = PRESETS.find((p) => p.id === range.presetId);
  return preset ? presetRange(preset, now) : { start: range.start, stop: range.stop };
}

export function presetRange(preset: RangePreset, now = new Date()): { start: Date; stop: Date } {
  return { start: new Date(now.getTime() - preset.seconds * 1000), stop: now };
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Nilai untuk `<input type="datetime-local">`, dalam waktu setempat. */
export function toInputValue(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fromInputValue(value: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function rangeError(start: Date | null, stop: Date | null): string | null {
  if (!start || !stop) return "Isi tanggal awal dan tanggal akhir.";
  if (stop.getTime() <= start.getTime()) return "Tanggal akhir harus setelah tanggal awal.";
  if ((stop.getTime() - start.getTime()) / 86_400_000 > MAX_RANGE_DAYS) {
    return `Rentang terlalu panjang, maksimal ${MAX_RANGE_DAYS} hari.`;
  }
  return null;
}

const formatDateTime = (date: Date) =>
  date.toLocaleString("id-ID", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

export function describeRange(start: Date, stop: Date): string {
  return `${formatDateTime(start)} sampai ${formatDateTime(stop)}`;
}

/** Sel CSV menurut RFC 4180. Teks yang berawalan rumus diberi tanda petik
 *  supaya spreadsheet tidak mengeksekusinya; angka tidak disentuh. */
function csvCell(value: number | string | null): string {
  if (value === null) return "";
  let text = String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function readingsToCsv(rows: ReadingRow[]): string {
  const header = "waktu_utc,sensor,metrik,field,nilai,satuan,kualitas";
  const lines = rows.map((r) =>
    [r.time, r.sensor_id, r.metric, r.field, r.value, r.unit, r.quality].map(csvCell).join(","),
  );
  return `${[header, ...lines].join("\r\n")}\r\n`;
}

const day = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

export function csvFileName(slug: string, start: Date, stop: Date): string {
  const safe = slug.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "kapal";
  return `logger-${safe}-${day(start)}-sampai-${day(stop)}.csv`;
}
