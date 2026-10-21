import { describe, expect, it } from "vitest";
import { needsAttention, shipStatus, sortShips, summarise } from "./status";
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

describe("sortShips", () => {
  const sehat = makeShip({ ship_id: "1", name: "KM Alpha" });
  const offline = makeShip({ ship_id: "2", name: "KM Bravo", last_batch_received_at: minutesAgo(500) });
  const terganggu = makeShip({ ship_id: "3", name: "KM Charlie", last_batch_received_at: minutesAgo(20) });
  const sinkron = makeShip({ ship_id: "4", name: "KM Delta", has_gap: true });
  const nonaktif = makeShip({ ship_id: "5", name: "KM Echo", is_active: false });

  it("yang perlu perhatian di atas, yang sehat di bawah, nonaktif paling akhir", () => {
    const urut = sortShips([nonaktif, sehat, sinkron, terganggu, offline]).map((s) => s.name);
    expect(urut).toEqual(["KM Bravo", "KM Charlie", "KM Delta", "KM Alpha", "KM Echo"]);
  });

  it("status yang sama diurutkan menurut nama", () => {
    const a = makeShip({ ship_id: "a", name: "KM Zulu" });
    const b = makeShip({ ship_id: "b", name: "KM Anggrek" });
    expect(sortShips([a, b]).map((s) => s.name)).toEqual(["KM Anggrek", "KM Zulu"]);
  });

  it("tidak mengubah array aslinya", () => {
    const asli = [sehat, offline];
    sortShips(asli);
    expect(asli.map((s) => s.name)).toEqual(["KM Alpha", "KM Bravo"]);
  });
});
