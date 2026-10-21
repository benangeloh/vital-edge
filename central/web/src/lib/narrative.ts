import type { FleetShip } from "./types";
import { shipStatus, type FleetSummary } from "./status";

/** Kalimat untuk operator: apa yang terjadi, lalu apa yang perlu dilakukan.
 *
 * Tabel menjawab "berapa" dan "kapan". Bagian ini menjawab "jadi apa", dalam
 * bahasa yang bisa dibaca orang yang baru membuka dashboard, tanpa harus tahu
 * apa itu sequence atau batch. */

const UNITS: Array<[limitSeconds: number, divisor: number, unit: string]> = [
  [60, 1, "detik"],
  [3600, 60, "menit"],
  [48 * 3600, 3600, "jam"],
  [Infinity, 86400, "hari"],
];

/** "5 menit lalu", atau null bila belum pernah ada kabar. */
export function agoPhrase(iso: string | null | undefined, now = Date.now()): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;

  const seconds = Math.max(0, Math.round((now - then) / 1000));
  for (const [limit, divisor, unit] of UNITS) {
    if (seconds < limit) return `${Math.max(1, Math.round(seconds / divisor))} ${unit} lalu`;
  }
  return null;
}

/** Penjelasan satu kapal yang perlu perhatian, plus langkah pertama yang masuk akal. */
export function attentionNote(ship: FleetShip, now = Date.now()): string {
  const status = shipStatus(ship, now);
  const lastData = agoPhrase(ship.last_telemetry_timestamp, now);
  const lastContact = agoPhrase(ship.last_batch_received_at, now);

  if (status === "offline") {
    if (!lastData && !lastContact) {
      return "Belum pernah mengirim data. Selesaikan pemasangan perangkat di kapal ini.";
    }
    return `Tidak ada data sejak ${lastData ?? lastContact}. Periksa koneksi dan perangkat di kapal.`;
  }

  if (status === "degraded") {
    return `Kabar terakhir ${lastContact ?? "belum ada"}. Pantau dulu, sinyal kapal mungkin sedang lemah.`;
  }

  if (status === "syncing") {
    const pending = ship.pending_estimate ?? 0;
    const parts: string[] = [];
    if (ship.has_gap) parts.push("Ada rentang data yang belum lengkap.");
    if (pending > 0) parts.push(`${pending.toLocaleString("id-ID")} data menunggu dikirim.`);
    parts.push("Kapal masih mengejar, tidak perlu tindakan kecuali berlangsung lama.");
    return parts.join(" ");
  }

  return "Tidak ada masalah.";
}

export interface BoardHeadline {
  /** Angka besar di papan: kapal yang sedang mengirim data. */
  count: number;
  /** Lanjutan kalimat setelah angka. */
  rest: string;
  /** Satu kalimat tentang sisanya. */
  note: string;
}

/** Kapal yang masih berkabar (online, sinkron, terganggu) dianggap mengirim data. */
export function boardHeadline(summary: FleetSummary): BoardHeadline {
  if (summary.total === 0) {
    return {
      count: 0,
      rest: "kapal terdaftar",
      note: "Tambahkan kapal pertama dari menu Kapal.",
    };
  }

  const count = summary.online + summary.syncing + summary.degraded;
  const noData = summary.offline;
  const parts: string[] = [];
  if (noData > 0) parts.push(`${noData} kapal tidak mengirim data.`);
  if (summary.inactive > 0) parts.push(`${summary.inactive} kapal dinonaktifkan.`);
  if (parts.length === 0) parts.push("Semua kapal mengirim data.");

  return {
    count,
    rest: `dari ${summary.total} kapal sedang mengirim data`,
    note: parts.join(" "),
  };
}

/** Satu frasa pendek untuk kolom "Kondisi" di tabel: apa keadaan kapal ini, tanpa saran. */
export function conditionPhrase(ship: FleetShip, now = Date.now()): string {
  const status = shipStatus(ship, now);
  const lastData = agoPhrase(ship.last_telemetry_timestamp, now);
  const lastContact = agoPhrase(ship.last_batch_received_at, now);

  switch (status) {
    case "inactive":
      return "Dinonaktifkan";
    case "offline":
      return lastData || lastContact
        ? `Tidak ada data sejak ${lastData ?? lastContact}`
        : "Belum pernah mengirim data";
    case "degraded":
      return `Kabar terakhir ${lastContact ?? "belum ada"}`;
    case "syncing": {
      const pending = ship.pending_estimate ?? 0;
      const parts: string[] = [];
      if (pending > 0) parts.push(`Mengejar ${pending.toLocaleString("id-ID")} data tertunda`);
      if (ship.has_gap) parts.push(pending > 0 ? "ada rentang belum lengkap" : "Ada rentang belum lengkap");
      return parts.join(", ") || "Mengejar data tertunda";
    }
    default:
      return "Data masuk normal";
  }
}
