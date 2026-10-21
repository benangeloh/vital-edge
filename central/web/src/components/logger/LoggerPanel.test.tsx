import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoggerPanel } from "./LoggerPanel";
import type { ReadingRow, SensorRow } from "@/lib/types";

const readingsState = vi.hoisted(() => ({
  current: { data: undefined, isLoading: false, error: null, isFetching: false } as Record<string, unknown>,
  lastArgs: [] as unknown[],
}));

vi.mock("@/lib/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/queries")>()),
  useShipSensors: () => ({
    data: [
      { sensor_id: "me_rpm", metric: "rpm", unit: "rpm", status: "active", display_name: null, tags: {}, last_seen_at: null },
      { sensor_id: "fw_temp", metric: "temperature", unit: "degC", status: "active", display_name: null, tags: {}, last_seen_at: null },
    ] satisfies SensorRow[],
  }),
  useReadings: (...args: unknown[]) => {
    readingsState.lastArgs = args;
    return readingsState.current;
  },
}));

const row = (over: Partial<ReadingRow> = {}): ReadingRow => ({
  time: "2026-10-21T05:00:02Z",
  sensor_id: "me_rpm",
  metric: "rpm",
  field: "value",
  value: 1450.5,
  unit: "rpm",
  quality: "good",
  ...over,
});

function setReadings(rows: ReadingRow[], hasMore = false) {
  readingsState.current = {
    data: { rows, has_more: hasMore, limit: 50, offset: 0, start: "2026-10-20T00:00:00Z", stop: "2026-10-21T00:00:00Z" },
    isLoading: false,
    error: null,
    isFetching: false,
  };
}

describe("LoggerPanel", () => {
  beforeEach(() => setReadings([row(), row({ time: "2026-10-21T05:00:01Z", value: 1449 })]));

  it("menampilkan satu baris per pembacaan dengan kolom yang bisa dipahami", () => {
    render(<LoggerPanel shipId="s1" shipSlug="SHIP-001" />);
    const table = screen.getByRole("table");
    for (const h of ["Waktu", "Sensor", "Metrik", "Nilai", "Kualitas"]) {
      expect(within(table).getByRole("columnheader", { name: h })).toBeInTheDocument();
    }
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getAllByText("me_rpm")).toHaveLength(2);
    expect(within(table).getByText("1450,5")).toBeInTheDocument();
    expect(within(table).getAllByText("Baik")).toHaveLength(2);
  });

  it("menyebut rentang yang sedang dilihat dengan kata", () => {
    render(<LoggerPanel shipId="s1" shipSlug="SHIP-001" />);
    expect(screen.getByText(/Menampilkan 2 pembacaan/)).toBeInTheDocument();
    expect(screen.getByText(/sampai/)).toBeInTheDocument();
  });

  it("pilihan sensor memuat semua sensor kapal dan meneruskannya ke query", async () => {
    render(<LoggerPanel shipId="s1" shipSlug="SHIP-001" />);
    await userEvent.click(screen.getByRole("combobox", { name: /Sensor/ }));
    const list = screen.getByRole("listbox");
    expect(within(list).getByRole("option", { name: /Semua sensor/ })).toBeInTheDocument();
    expect(within(list).getByRole("option", { name: /fw_temp/ })).toBeInTheDocument();
    await userEvent.click(within(list).getByRole("option", { name: /fw_temp/ }));
    const opts = readingsState.lastArgs[1] as { sensorId?: string };
    expect(opts.sensorId).toBe("fw_temp");
  });

  it("rentang kosong menjelaskan apa yang bisa dilakukan, bukan tabel kosong", () => {
    setReadings([]);
    render(<LoggerPanel shipId="s1" shipSlug="SHIP-001" />);
    expect(screen.getByText(/Belum ada pembacaan pada rentang ini/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("galat dari server ditampilkan dengan tombol coba lagi", () => {
    readingsState.current = { data: undefined, isLoading: false, error: new Error("Server sibuk"), isFetching: false, refetch: vi.fn() };
    render(<LoggerPanel shipId="s1" shipSlug="SHIP-001" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Server sibuk");
    expect(screen.getByRole("button", { name: "Coba lagi" })).toBeInTheDocument();
  });

  it("halaman berikutnya hanya aktif bila masih ada data", async () => {
    setReadings([row()], true);
    render(<LoggerPanel shipId="s1" shipSlug="SHIP-001" />);
    const next = screen.getByRole("button", { name: "Berikutnya" });
    expect(next).toBeEnabled();
    expect(screen.getByRole("button", { name: "Sebelumnya" })).toBeDisabled();
    await userEvent.click(next);
    expect((readingsState.lastArgs[1] as { offset: number }).offset).toBe(50);
  });

  it("tombol unduh CSV tersedia dan menyebut batasnya", () => {
    render(<LoggerPanel shipId="s1" shipSlug="SHIP-001" />);
    expect(screen.getByRole("button", { name: /Unduh CSV/ })).toBeInTheDocument();
    expect(screen.getByText(/maksimal 5\.000 baris/i)).toBeInTheDocument();
  });
});
