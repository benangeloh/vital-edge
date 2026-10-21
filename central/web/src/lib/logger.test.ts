import { describe, expect, it } from "vitest";
import {
  PRESETS,
  csvFileName,
  describeRange,
  fromInputValue,
  presetRange,
  resolveRange,
  readingsToCsv,
  rangeError,
  toInputValue,
} from "./logger";
import type { ReadingRow } from "./types";

const NOW = new Date("2026-10-21T10:00:00Z");

describe("presetRange", () => {
  it("berakhir sekarang dan mundur sesuai pilihan", () => {
    const six = PRESETS.find((p) => p.id === "6h")!;
    const range = presetRange(six, NOW);
    expect(range.stop.getTime()).toBe(NOW.getTime());
    expect(NOW.getTime() - range.start.getTime()).toBe(6 * 3600 * 1000);
  });

  it("punya pilihan dari satu jam sampai tiga puluh hari", () => {
    expect(PRESETS.map((p) => p.id)).toEqual(["1h", "6h", "24h", "7d", "30d"]);
  });
});

describe("nilai input datetime-local", () => {
  it("bolak-balik tanpa bergeser", () => {
    const date = new Date(2026, 9, 21, 8, 30);
    expect(fromInputValue(toInputValue(date))?.getTime()).toBe(date.getTime());
  });

  it("memakai bentuk YYYY-MM-DDTHH:mm yang dimengerti browser", () => {
    expect(toInputValue(new Date(2026, 0, 5, 7, 4))).toBe("2026-01-05T07:04");
  });

  it("isian kosong atau rusak menghasilkan null, bukan tanggal salah", () => {
    expect(fromInputValue("")).toBeNull();
    expect(fromInputValue("bukan tanggal")).toBeNull();
  });
});

describe("rangeError", () => {
  const start = new Date("2026-10-20T00:00:00Z");

  it("rentang wajar tidak bermasalah", () => {
    expect(rangeError(start, new Date("2026-10-21T00:00:00Z"))).toBeNull();
  });

  it("tanggal akhir sebelum atau sama dengan awal dijelaskan", () => {
    expect(rangeError(start, start)).toMatch(/setelah tanggal awal/);
    expect(rangeError(start, new Date("2026-10-19T00:00:00Z"))).toMatch(/setelah tanggal awal/);
  });

  it("rentang lebih dari 400 hari dijelaskan", () => {
    expect(rangeError(start, new Date("2028-01-01T00:00:00Z"))).toMatch(/400 hari/);
  });

  it("tanggal belum diisi dijelaskan", () => {
    expect(rangeError(null, start)).toMatch(/isi/i);
    expect(rangeError(start, null)).toMatch(/isi/i);
  });
});

describe("describeRange", () => {
  it("menulis rentang dengan kata, tanpa tanda strip", () => {
    const text = describeRange(new Date(2026, 9, 20, 8, 0), new Date(2026, 9, 21, 8, 0));
    expect(text).toContain("sampai");
    expect(text).not.toContain("—");
  });
});

const rows: ReadingRow[] = [
  {
    time: "2026-10-21T05:00:02Z",
    sensor_id: "me_rpm",
    metric: "rpm",
    field: "value",
    value: 1450.5,
    unit: "rpm",
    quality: "good",
  },
  {
    time: "2026-10-21T05:00:01Z",
    sensor_id: 'sensor "x", a',
    metric: "state",
    field: "value",
    value: "ON, siap",
    unit: null,
    quality: null,
  },
];

describe("readingsToCsv", () => {
  it("memuat judul kolom berbahasa Indonesia dan satu baris per pembacaan", () => {
    const lines = readingsToCsv(rows).trimEnd().split("\r\n");
    expect(lines[0]).toBe("waktu_utc,sensor,metrik,field,nilai,satuan,kualitas");
    expect(lines).toHaveLength(3);
    expect(lines[1]).toBe("2026-10-21T05:00:02Z,me_rpm,rpm,value,1450.5,rpm,good");
  });

  it("mengutip sel yang berisi koma atau tanda kutip, sesuai RFC 4180", () => {
    const line = readingsToCsv(rows).trimEnd().split("\r\n")[2]!;
    expect(line).toBe('2026-10-21T05:00:01Z,"sensor ""x"", a",state,value,"ON, siap",,');
  });

  it("sel yang berawalan rumus tidak dieksekusi spreadsheet", () => {
    const csv = readingsToCsv([{ ...rows[0]!, sensor_id: "=HYPERLINK(\"http://x\")" }]);
    expect(csv).toContain("'=HYPERLINK");
  });
});

describe("csvFileName", () => {
  it("memuat kapal dan tanggal rentang", () => {
    const name = csvFileName("SHIP-001", new Date(2026, 9, 20, 8), new Date(2026, 9, 21, 8));
    expect(name).toBe("logger-SHIP-001-2026-10-20-sampai-2026-10-21.csv");
  });

  it("slug dibersihkan dari karakter yang tidak aman untuk nama berkas", () => {
    const name = csvFileName("../a b", new Date(2026, 9, 20), new Date(2026, 9, 21));
    expect(name).not.toMatch(/[/\\ ]/);
  });
});

describe("resolveRange", () => {
  const start = new Date("2026-10-01T00:00:00Z");
  const stop = new Date("2026-10-02T00:00:00Z");

  it("rentang sendiri dipakai apa adanya", () => {
    expect(resolveRange({ presetId: null, start, stop }, NOW)).toEqual({ start, stop });
  });

  it("pilihan cepat dihitung ulang dari sekarang, bukan dari waktu dipilih", () => {
    const r = resolveRange({ presetId: "1h", start, stop }, NOW);
    expect(r.stop.getTime()).toBe(NOW.getTime());
    expect(r.start.getTime()).toBe(NOW.getTime() - 3600_000);
  });
});
