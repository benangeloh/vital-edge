import { describe, expect, it } from "vitest";
import { compactNumber, relativeTime, shortId, staleness } from "./format";

describe("relativeTime", () => {
  const now = new Date("2026-08-31T12:00:00Z").getTime();

  it.each([
    ["2026-08-31T11:59:30Z", "30 dtk"],
    ["2026-08-31T11:45:00Z", "15 mnt"],
    ["2026-08-31T09:00:00Z", "3 jam"],
    ["2026-08-28T12:00:00Z", "3 hr"],
  ])("%s -> %s", (iso, expected) => {
    expect(relativeTime(iso, now)).toBe(expected);
  });

  it("nilai kosong ditampilkan sebagai strip, bukan error", () => {
    expect(relativeTime(null, now)).toBe("—");
    expect(relativeTime("bukan-tanggal", now)).toBe("—");
  });
});

describe("staleness", () => {
  const now = new Date("2026-08-31T12:00:00Z").getTime();

  it("membedakan segar, tertinggal, dan basi", () => {
    expect(staleness("2026-08-31T11:59:00Z", now)).toBe("fresh");
    expect(staleness("2026-08-31T11:30:00Z", now)).toBe("stale");
    expect(staleness("2026-08-31T09:00:00Z", now)).toBe("old");
    expect(staleness(null, now)).toBe("none");
  });
});

describe("compactNumber", () => {
  it("memendekkan angka besar agar kolom tidak melebar", () => {
    expect(compactNumber(950)).toBe("950");
    expect(compactNumber(1500)).toBe("1.5 rb");
    expect(compactNumber(2_400_000)).toBe("2.4 jt");
    expect(compactNumber(null)).toBe("—");
  });
});

describe("shortId", () => {
  it("memotong id panjang", () => {
    expect(shortId("11111111-1111-1111", 8)).toBe("11111111");
    expect(shortId(null)).toBe("—");
  });
});
