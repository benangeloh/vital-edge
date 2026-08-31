import { render } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { FleetBar } from "@/components/FleetBar";
import { ShipTable } from "@/components/ShipTable";
import { Sidebar } from "@/components/shell/Sidebar";
import { makeShip, minutesAgo } from "./factories";
import type { FleetSummary } from "@/lib/status";

/** Pemeriksaan aksesibilitas dasar dengan axe-core.
 *
 * Bukan pengganti pengujian manual, tetapi menangkap kelas kesalahan yang paling
 * sering lolos: kontras warna, label yang hilang, struktur tabel yang salah, dan
 * urutan heading. Dashboard ini dipakai berjam-jam setiap hari — kesalahan
 * semacam itu terasa. */
async function checkA11y(ui: React.ReactElement) {
  const { container } = render(ui);
  const results = await axe.run(container, {
    rules: {
      // Butuh dokumen lengkap; komponen diuji terisolasi di sini.
      region: { enabled: false },
      "page-has-heading-one": { enabled: false },
    },
  });
  return results.violations;
}

const summary: FleetSummary = {
  total: 70, online: 52, syncing: 6, degraded: 4, offline: 7, inactive: 1,
  pendingRecords: 12_400,
};

describe("aksesibilitas", () => {
  it("tabel armada tanpa pelanggaran", async () => {
    const violations = await checkA11y(
      <MemoryRouter>
        <ShipTable
          ships={[
            makeShip(),
            makeShip({ ship_id: "b", name: "KM Dua", last_batch_received_at: minutesAgo(200) }),
            makeShip({ ship_id: "c", name: "KM Tiga", has_gap: true, pending_estimate: 9000 }),
          ]}
        />
      </MemoryRouter>,
    );
    expect(violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });

  it("batang armada tanpa pelanggaran", async () => {
    const violations = await checkA11y(<FleetBar summary={summary} />);
    expect(violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });

  it("navigasi tanpa pelanggaran", async () => {
    const violations = await checkA11y(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
    expect(violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });
});
