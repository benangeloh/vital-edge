import { describe, expect, it } from "vitest";
import { agoPhrase, attentionNote, boardHeadline, conditionPhrase } from "./narrative";
import type { FleetSummary } from "./status";
import { makeShip, minutesAgo } from "@/test/factories";

const NOW = Date.parse("2026-10-21T10:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

describe("agoPhrase", () => {
  it("belum pernah ada data menghasilkan null, bukan teks aneh", () => {
    expect(agoPhrase(null, NOW)).toBeNull();
    expect(agoPhrase("bukan-tanggal", NOW)).toBeNull();
  });

  it.each([
    [30, "30 detik lalu"],
    [60, "1 menit lalu"],
    [5 * 60, "5 menit lalu"],
    [3 * 3600, "3 jam lalu"],
    [50 * 86400, "50 hari lalu"],
  ])("%i detik menjadi %s", (seconds, expected) => {
    expect(agoPhrase(ago(seconds), NOW)).toBe(expected);
  });
});

describe("attentionNote", () => {
  it("kapal offline yang pernah mengirim menyebut sejak kapan dan apa yang diperiksa", () => {
    const note = attentionNote(
      makeShip({
        last_telemetry_timestamp: ago(50 * 86400),
        last_batch_received_at: ago(50 * 86400),
      }),
      NOW,
    );
    expect(note).toContain("Tidak ada data sejak 50 hari lalu");
    expect(note).toMatch(/periksa/i);
  });

  it("kapal yang belum pernah mengirim tidak diberi kalimat 'lalu' yang kosong", () => {
    const note = attentionNote(
      makeShip({ last_telemetry_timestamp: null, last_batch_received_at: null }),
      NOW,
    );
    expect(note).toContain("Belum pernah mengirim data");
    expect(note).not.toContain("lalu");
  });

  it("celah sequence dijelaskan dengan bahasa biasa", () => {
    const note = attentionNote(
      makeShip({ has_gap: true, last_batch_received_at: ago(30), last_telemetry_timestamp: ago(30) }),
      NOW,
    );
    expect(note).toContain("belum lengkap");
  });

  it("tunggakan besar menyebut jumlahnya dengan format Indonesia", () => {
    const note = attentionNote(
      makeShip({
        pending_estimate: 5000,
        last_batch_received_at: ago(30),
        last_telemetry_timestamp: ago(30),
      }),
      NOW,
    );
    expect(note).toContain("5.000 data menunggu dikirim");
  });

  it("kapal yang terganggu menyebut kabar terakhirnya", () => {
    const note = attentionNote(
      makeShip({
        last_batch_received_at: minutesAgo(20),
        last_telemetry_timestamp: minutesAgo(20),
      }),
      Date.now(),
    );
    expect(note).toContain("20 menit lalu");
  });

  it("tidak pernah memakai tanda pisah em dash", () => {
    for (const ship of [
      makeShip({ last_batch_received_at: null, last_telemetry_timestamp: null }),
      makeShip({ has_gap: true }),
      makeShip({ pending_estimate: 9000 }),
    ]) {
      expect(attentionNote(ship, NOW)).not.toContain("—");
    }
  });
});

describe("boardHeadline", () => {
  const base: FleetSummary = {
    total: 7, online: 1, syncing: 0, degraded: 0, offline: 6, inactive: 0, pendingRecords: 0,
  };

  it("menghitung kapal yang sedang mengirim data, termasuk yang masih sinkron atau terganggu", () => {
    const h = boardHeadline({ ...base, online: 3, syncing: 2, degraded: 1, offline: 1 });
    expect(h.count).toBe(6);
    expect(h.rest).toBe("dari 7 kapal sedang mengirim data");
  });

  it("menjelaskan sisanya dalam satu kalimat", () => {
    expect(boardHeadline(base).note).toBe("6 kapal tidak mengirim data.");
    expect(boardHeadline({ ...base, online: 7, offline: 0 }).note).toBe("Semua kapal mengirim data.");
  });

  it("armada kosong tidak membuat kalimat 0 dari 0", () => {
    const h = boardHeadline({ ...base, total: 0, online: 0, offline: 0 });
    expect(h.rest).toBe("kapal terdaftar");
    expect(h.note).toMatch(/tambahkan/i);
  });

  it("kapal nonaktif tidak dihitung sebagai tidak mengirim data", () => {
    const h = boardHeadline({ ...base, total: 3, online: 2, offline: 0, inactive: 1 });
    expect(h.note).toBe("1 kapal dinonaktifkan.");
  });
});

describe("conditionPhrase", () => {
  it("kapal sehat menyatakan data masuk normal", () => {
    const ship = makeShip({
      last_batch_received_at: ago(20),
      last_telemetry_timestamp: ago(20),
    });
    expect(conditionPhrase(ship, NOW)).toBe("Data masuk normal");
  });

  it("kapal offline menyebut sejak kapan, atau belum pernah", () => {
    expect(
      conditionPhrase(
        makeShip({ last_telemetry_timestamp: ago(3 * 3600), last_batch_received_at: ago(3 * 3600) }),
        NOW,
      ),
    ).toBe("Tidak ada data sejak 3 jam lalu");
    expect(
      conditionPhrase(makeShip({ last_telemetry_timestamp: null, last_batch_received_at: null }), NOW),
    ).toBe("Belum pernah mengirim data");
  });

  it("kapal sinkron menyebut tunggakan dan celah dengan kata biasa", () => {
    const ship = makeShip({
      pending_estimate: 1200,
      has_gap: true,
      last_batch_received_at: ago(10),
      last_telemetry_timestamp: ago(10),
    });
    expect(conditionPhrase(ship, NOW)).toBe("Mengejar 1.200 data tertunda, ada rentang belum lengkap");
  });

  it("kapal nonaktif dinyatakan dinonaktifkan", () => {
    expect(conditionPhrase(makeShip({ is_active: false }), NOW)).toBe("Dinonaktifkan");
  });
});
