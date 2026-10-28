import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ShipTable } from "./ShipTable";
import { makeShip, minutesAgo } from "@/test/factories";

function renderTable(ships = [makeShip()]) {
  return render(
    <MemoryRouter>
      <ShipTable ships={ships} />
    </MemoryRouter>,
  );
}

describe("ShipTable", () => {
  it("menampilkan nama kapal sebagai tautan ke detailnya", () => {
    renderTable();
    const link = screen.getByRole("link", { name: "KM Sinar Jaya" });
    expect(link).toHaveAttribute("href", "/ships/11111111-1111-1111-1111-111111111111");
  });

  it("memuat kolom yang dibutuhkan untuk menilai satu kapal", () => {
    renderTable();
    for (const header of ["Status", "Kapal", "Kondisi", "Update Terakhir", "Data Tertunda", "Aksi"]) {
      expect(screen.getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
  });

  it("status tidak hanya bergantung warna, ada label teks", () => {
    // Penting untuk pengguna buta warna dan untuk layar anjungan yang silau.
    renderTable([makeShip({ last_batch_received_at: minutesAgo(200) })]);
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });

  it("kondisi ditulis sebagai kalimat, bukan kode", () => {
    renderTable([makeShip({ last_batch_received_at: minutesAgo(200), last_telemetry_timestamp: minutesAgo(200) })]);
    expect(screen.getByText(/Tidak ada data sejak 3 jam lalu/)).toBeInTheDocument();
  });

  it("tombol Lihat detail membawa nama kapal untuk pembaca layar", () => {
    renderTable();
    const link = screen.getByRole("link", { name: "Lihat detail KM Sinar Jaya" });
    expect(link).toHaveAttribute("href", "/ships/11111111-1111-1111-1111-111111111111");
  });

  it("kapal tanpa tunggakan menampilkan kata, bukan nol atau tanda strip", () => {
    renderTable([makeShip({ pending_estimate: 0 })]);
    const row = screen.getByRole("row", { name: /KM Sinar Jaya/ });
    expect(within(row).getAllByText("Tidak ada").length).toBeGreaterThan(0);
  });

  it("tunggakan besar ditandai secara visual", () => {
    const { container } = renderTable([makeShip({ pending_estimate: 5000 })]);
    expect(container.querySelector(".pending--high")).not.toBeNull();
  });

  it("celah sequence ikut disebut di kolom kondisi", () => {
    renderTable([makeShip({ has_gap: true })]);
    expect(screen.getByText(/ada rentang belum lengkap/i)).toBeInTheDocument();
  });

  it("daftar kosong memberi pesan, bukan tabel kosong tanpa penjelasan", () => {
    renderTable([]);
    expect(screen.getByText(/Tidak ada kapal yang cocok/)).toBeInTheDocument();
  });

  it("tabel punya caption untuk pembaca layar", () => {
    renderTable();
    expect(screen.getByRole("table", { name: /Daftar kapal beserta status/ })).toBeInTheDocument();
  });

  describe("menu aksi kapal", () => {
    const actions = () => ({
      onCredential: vi.fn(),
      onEdit: vi.fn(),
      onDecommission: vi.fn(),
    });

    function renderWithActions(a = actions(), ships = [makeShip()]) {
      render(
        <MemoryRouter>
          <ShipTable ships={ships} actions={a} />
        </MemoryRouter>,
      );
      return a;
    }

    it("tidak tampil bila pemakai tidak boleh mengelola", () => {
      renderTable();
      expect(screen.queryByRole("button", { name: /Aksi lainnya/ })).not.toBeInTheDocument();
    });

    it("hanya satu tombol menu per baris, bukan deretan tombol", () => {
      renderWithActions();
      expect(screen.getByRole("button", { name: "Aksi lainnya untuk KM Sinar Jaya" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Edit/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Nonaktifkan/ })).not.toBeInTheDocument();
    });

    it("menu memuat kredensial, edit, dan nonaktifkan", async () => {
      renderWithActions();
      await userEvent.setup().click(screen.getByRole("button", { name: "Aksi lainnya untuk KM Sinar Jaya" }));
      expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual([
        "Kredensial & skrip pasang",
        "Edit data kapal",
        "Nonaktifkan kapal",
      ]);
    });

    it("memanggil aksi dengan kapal pada barisnya", async () => {
      const kapal = makeShip({ ship_id: "22222222-2222-2222-2222-222222222222", name: "KM Dua" });
      const a = renderWithActions(actions(), [makeShip(), kapal]);
      const user = userEvent.setup();
      const buka = () => user.click(screen.getByRole("button", { name: "Aksi lainnya untuk KM Dua" }));

      await buka();
      await user.click(screen.getByRole("menuitem", { name: "Edit data kapal" }));
      await buka();
      await user.click(screen.getByRole("menuitem", { name: "Kredensial & skrip pasang" }));
      await buka();
      await user.click(screen.getByRole("menuitem", { name: "Nonaktifkan kapal" }));

      expect(a.onEdit).toHaveBeenCalledWith(kapal);
      expect(a.onCredential).toHaveBeenCalledWith(kapal);
      expect(a.onDecommission).toHaveBeenCalledWith(kapal);
    });

    it("tautan Lihat detail tetap ada", () => {
      renderWithActions();
      expect(screen.getByRole("link", { name: "Lihat detail KM Sinar Jaya" })).toBeInTheDocument();
    });
  });
});
