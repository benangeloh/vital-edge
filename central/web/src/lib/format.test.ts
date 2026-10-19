import { describe, expect, it } from "vitest";
import { compactNumber, formatBytes, formatDuration, relativeTime, shortId, staleness } from "./format";

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

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [1536, "1,5 KB"],
    [5 * 1024 ** 2, "5,0 MB"],
    [250 * 1024 ** 3, "250 GB"],
    [1.5 * 1024 ** 4, "1,5 TB"],
  ])("%s -> %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });

  it("nilai kosong atau tidak valid ditampilkan sebagai strip", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(undefined)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
  });
});

describe("formatDuration", () => {
  it.each([
    [45, "45 dtk"],
    [600, "10 mnt"],
    [3 * 3600 + 5 * 60, "3 jam 5 mnt"],
    [2 * 86_400 + 4 * 3600, "2 hr 4 jam"],
  ])("%s dtk -> %s", (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });

  it("nilai kosong ditampilkan sebagai strip", () => {
    expect(formatDuration(null)).toBe("—");
  });
});
