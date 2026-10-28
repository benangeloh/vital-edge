import { render } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FleetStats } from "@/components/FleetStats";
import { OnboardShipDialog } from "@/components/OnboardShipDialog";
import { ShipTable } from "@/components/ShipTable";
import { TopBar } from "@/components/shell/TopBar";
import { makeShip, minutesAgo } from "./factories";
import { summarise } from "@/lib/status";

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

  it("ringkasan armada tanpa pelanggaran", async () => {
    const ships = [
      makeShip(),
      makeShip({ ship_id: "b", name: "KM Dua", last_batch_received_at: minutesAgo(200) }),
      makeShip({ ship_id: "c", name: "KM Tiga", has_gap: true, pending_estimate: 9000 }),
    ];
    const violations = await checkA11y(
      <FleetStats summary={summarise(ships)} selected="all" onSelect={() => {}} />,
    );
    expect(violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });

  it("dialog tambah kapal tanpa pelanggaran", async () => {
    // Formulir adalah tempat pelanggaran aksesibilitas paling sering muncul:
    // input tanpa label, kontras teks bantuan, dan dialog tanpa nama.
    const client = new QueryClient();
    const violations = await checkA11y(
      <QueryClientProvider client={client}>
        <OnboardShipDialog onClose={() => {}} />
      </QueryClientProvider>,
    );
    expect(violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });

  it("bilah atas dan navigasi tanpa pelanggaran", async () => {
    const violations = await checkA11y(
      <MemoryRouter>
        <TopBar
          summary={{ total: 3, online: 1, syncing: 0, degraded: 0, offline: 2, inactive: 0, pendingRecords: 0 }}
          attentionCount={2}
          search=""
          onSearch={() => {}}
        />
      </MemoryRouter>,
    );
    expect(violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });
});
