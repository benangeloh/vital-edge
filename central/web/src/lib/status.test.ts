import { describe, expect, it } from "vitest";
import { needsAttention, shipStatus, summarise } from "./status";
import { makeShip, minutesAgo } from "@/test/factories";

/** Status gabungan inilah jawaban atas "kapal mana yang bermasalah".
 *  Kalau logikanya salah, operator melihat armada yang sehat padahal tidak. */
describe("shipStatus", () => {
  it("kapal yang baru mengirim data dianggap online", () => {
    expect(shipStatus(makeShip())).toBe("online");
  });

  it("kapal yang lama tidak mengirim dianggap offline", () => {
    expect(shipStatus(makeShip({ last_batch_received_at: minutesAgo(120) }))).toBe("offline");
  });

  it("kapal tanpa data sama sekali dianggap offline", () => {
    expect(shipStatus(makeShip({ last_batch_received_at: null }))).toBe("offline");
  });

  it("tunggakan besar berarti sedang sinkron, bukan sehat", () => {
    expect(shipStatus(makeShip({ pending_estimate: 5000 }))).toBe("syncing");
  });

  it("celah sequence berarti sedang sinkron", () => {
    expect(shipStatus(makeShip({ has_gap: true }))).toBe("syncing");
  });

  it("data agak tertinggal berarti terganggu, belum offline", () => {
    expect(shipStatus(makeShip({ last_batch_received_at: minutesAgo(20) }))).toBe("degraded");
  });

  it("kapal nonaktif tidak dihitung sebagai masalah", () => {
    expect(shipStatus(makeShip({ is_active: false, last_batch_received_at: null }))).toBe(
      "inactive",
    );
  });
});

describe("summarise", () => {
  it("menghitung setiap status dan total tunggakan", () => {
    const summary = summarise([
      makeShip(),
      makeShip({ ship_id: "b", last_batch_received_at: minutesAgo(120) }),
      makeShip({ ship_id: "c", pending_estimate: 900 }),
    ]);
    expect(summary.total).toBe(3);
    expect(summary.online).toBe(1);
    expect(summary.offline).toBe(1);
    expect(summary.syncing).toBe(1);
    expect(summary.pendingRecords).toBe(900);
  });

  it("armada kosong tidak membuat perhitungan gagal", () => {
    expect(summarise([]).total).toBe(0);
  });
});

describe("needsAttention", () => {
  it("hanya memuat kapal bermasalah, offline lebih dulu", () => {
    const list = needsAttention([
      makeShip({ ship_id: "sehat" }),
      makeShip({ ship_id: "sinkron", pending_estimate: 900 }),
      makeShip({ ship_id: "offline", last_batch_received_at: minutesAgo(200) }),
    ]);
    expect(list.map((s) => s.ship_id)).toEqual(["offline", "sinkron"]);
  });

  it("armada sehat menghasilkan daftar kosong", () => {
    // Panel "perlu perhatian" karena itu tidak ditampilkan sama sekali —
    // panel "semua baik" yang selalu tampil melatih mata mengabaikannya.
    expect(needsAttention([makeShip(), makeShip({ ship_id: "b" })])).toEqual([]);
  });

  it("di antara sesama offline, tunggakan terbesar lebih dulu", () => {
    const list = needsAttention([
      makeShip({ ship_id: "kecil", last_batch_received_at: minutesAgo(200), pending_estimate: 10 }),
      makeShip({ ship_id: "besar", last_batch_received_at: minutesAgo(200), pending_estimate: 999 }),
    ]);
    expect(list[0]?.ship_id).toBe("besar");
  });
});
