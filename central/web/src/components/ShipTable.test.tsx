import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
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

  it("memuat semua kolom yang disyaratkan", () => {
    renderTable();
    for (const header of [
      "Kapal", "Status", "Data Terakhir", "Sync Terakhir",
      "Data Tertunda", "Aliran Data", "Alert",
    ]) {
      expect(screen.getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
  });

  it("status tidak hanya bergantung warna — ada label teks", () => {
    // Penting untuk pengguna buta warna dan untuk layar anjungan yang silau.
    renderTable([makeShip({ last_batch_received_at: minutesAgo(200) })]);
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });

  it("kapal tanpa tunggakan menampilkan strip, bukan nol", () => {
    renderTable([makeShip({ pending_estimate: 0 })]);
    const row = screen.getByRole("row", { name: /KM Sinar Jaya/ });
    expect(within(row).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("tunggakan besar ditandai secara visual", () => {
    const { container } = renderTable([makeShip({ pending_estimate: 5000 })]);
    expect(container.querySelector(".pending--high")).not.toBeNull();
  });

  it("celah sequence ditampilkan, bukan disembunyikan", () => {
    renderTable([makeShip({ has_gap: true })]);
    expect(screen.getByText("ada celah")).toBeInTheDocument();
  });

  it("kolom aliran data tidak mengulang status koneksi mentah", () => {
    // Server menurunkan connection_state dari waktu kabar terakhir, sama seperti
    // kolom Status. Menampilkan keduanya menghasilkan baris yang bertentangan
    // dengan dirinya sendiri — "Terganggu" di sebelah "offline".
    renderTable([makeShip({ connection_state: "offline", has_gap: false })]);
    expect(screen.getByText("utuh")).toBeInTheDocument();
    expect(screen.queryByText("offline")).not.toBeInTheDocument();
  });

  it("daftar kosong memberi pesan, bukan tabel kosong tanpa penjelasan", () => {
    renderTable([]);
    expect(screen.getByText(/Tidak ada kapal yang cocok/)).toBeInTheDocument();
  });

  it("tabel punya caption untuk pembaca layar", () => {
    renderTable();
    expect(screen.getByRole("table", { name: /Daftar kapal beserta status/ })).toBeInTheDocument();
  });
});
