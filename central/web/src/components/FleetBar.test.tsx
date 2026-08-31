import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FleetBar } from "./FleetBar";
import type { FleetSummary } from "@/lib/status";

const summary: FleetSummary = {
  total: 70, online: 52, syncing: 6, degraded: 4, offline: 7, inactive: 1,
  pendingRecords: 12_400,
};

describe("FleetBar", () => {
  it("menjawab 'berapa kapal' dengan satu angka besar", () => {
    render(<FleetBar summary={summary} />);
    expect(screen.getByText("70")).toBeInTheDocument();
    expect(screen.getByText(/kapal dalam armada/)).toBeInTheDocument();
  });

  it("menampilkan jumlah setiap status", () => {
    render(<FleetBar summary={summary} />);
    for (const [label, count] of [
      ["Online", "52"], ["Sinkron", "6"], ["Terganggu", "4"], ["Offline", "7"],
    ] as const) {
      const term = screen.getByText(label);
      expect(term.parentElement?.parentElement).toHaveTextContent(count);
    }
  });

  it("batang proporsional punya label teks untuk pembaca layar", () => {
    // Batangnya visual; tanpa label ini, informasinya hilang sepenuhnya bagi
    // pengguna pembaca layar.
    render(<FleetBar summary={summary} />);
    const bar = screen.getByRole("img");
    expect(bar).toHaveAttribute("aria-label", expect.stringContaining("52 Online"));
  });

  it("armada kosong tidak membuat pembagian dengan nol", () => {
    const empty: FleetSummary = {
      total: 0, online: 0, syncing: 0, degraded: 0, offline: 0, inactive: 0, pendingRecords: 0,
    };
    expect(() => render(<FleetBar summary={empty} />)).not.toThrow();
  });
});
