import type { FleetShip } from "./types";
import { staleness } from "./format";

/** Status operasional sebuah kapal, gabungan dari beberapa sinyal.
 *
 * Ini bukan sekadar `connection_state` dari server. Seorang operator yang
 * bertanya "kapal mana yang bermasalah" perlu satu jawaban, bukan tiga kolom
 * yang harus ia gabungkan sendiri di kepalanya. */
export type ShipStatus = "online" | "syncing" | "degraded" | "offline" | "inactive";

export interface StatusMeta {
  label: string;
  /** Simbol, supaya status tidak hanya bergantung warna — penting untuk
   *  pengguna buta warna dan untuk layar anjungan yang terkena matahari. */
  glyph: string;
  tone: "ok" | "warn" | "crit" | "idle" | "sync";
}

export const STATUS_META: Record<ShipStatus, StatusMeta> = {
  online: { label: "Online", glyph: "●", tone: "ok" },
  syncing: { label: "Sinkron", glyph: "◓", tone: "sync" },
  degraded: { label: "Terganggu", glyph: "◐", tone: "warn" },
  offline: { label: "Offline", glyph: "○", tone: "idle" },
  inactive: { label: "Nonaktif", glyph: "◌", tone: "idle" },
};

const PENDING_BACKLOG_THRESHOLD = 500;

export function shipStatus(ship: FleetShip, now = Date.now()): ShipStatus {
  if (!ship.is_active) return "inactive";

  const age = staleness(ship.last_batch_received_at, now);
  if (age === "none" || age === "old") return "offline";

  // Ada tunggakan besar atau celah sequence: kapalnya terhubung, tetapi belum
  // selesai menyetor. Itu keadaan yang berbeda dari "sehat" maupun "offline",
  // dan operator perlu bisa membedakannya.
  const pending = ship.pending_estimate ?? 0;
  if (ship.has_gap || pending > PENDING_BACKLOG_THRESHOLD) return "syncing";

  if (age === "stale" || ship.connection_state === "degraded") return "degraded";
  return "online";
}

/** Kapal yang butuh perhatian, diurutkan dari yang paling gawat.
 *
 * Halaman utama menampilkan ini hanya bila memang ada isinya. Panel "tidak ada
 * masalah" yang selalu tampil justru melatih mata untuk mengabaikannya. */
export function needsAttention(ships: FleetShip[], now = Date.now()): FleetShip[] {
  const severity: Record<ShipStatus, number> = {
    offline: 0,
    degraded: 1,
    syncing: 2,
    inactive: 9,
    online: 9,
  };
  return ships
    .filter((s) => {
      const status = shipStatus(s, now);
      return status === "offline" || status === "degraded" || status === "syncing";
    })
    .sort((a, b) => {
      const diff = severity[shipStatus(a, now)] - severity[shipStatus(b, now)];
      if (diff !== 0) return diff;
      return (b.pending_estimate ?? 0) - (a.pending_estimate ?? 0);
    });
}

export interface FleetSummary {
  total: number;
  online: number;
  syncing: number;
  degraded: number;
  offline: number;
  inactive: number;
  pendingRecords: number;
}

export function summarise(ships: FleetShip[], now = Date.now()): FleetSummary {
  const summary: FleetSummary = {
    total: ships.length,
    online: 0,
    syncing: 0,
    degraded: 0,
    offline: 0,
    inactive: 0,
    pendingRecords: 0,
  };
  for (const ship of ships) {
    summary[shipStatus(ship, now)] += 1;
    summary.pendingRecords += ship.pending_estimate ?? 0;
  }
  return summary;
}
