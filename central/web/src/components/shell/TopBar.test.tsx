import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { TopBar } from "./TopBar";
import type { FleetSummary } from "@/lib/status";

const SUMMARY: FleetSummary = {
  total: 9, online: 1, syncing: 0, degraded: 0, offline: 8, inactive: 0, pendingRecords: 0,
};

function renderBar(
  over: Partial<React.ComponentProps<typeof TopBar>> = {},
  route = "/",
) {
  const onSearch = vi.fn();
  render(
    <MemoryRouter initialEntries={[route]}>
      <TopBar summary={SUMMARY} attentionCount={0} search="" onSearch={onSearch} {...over} />
    </MemoryRouter>,
  );
  return { onSearch };
}

describe("TopBar", () => {
  it("merek menaut ke ringkasan armada", () => {
    renderBar();
    expect(screen.getByRole("link", { name: /FleetView/ })).toHaveAttribute("href", "/");
  });

  it("navigasi utama berisi dua modul, tanpa sidebar", () => {
    renderBar();
    const nav = screen.getByRole("navigation", { name: "Navigasi utama" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Ringkasan",
      "Kapal",
    ]);
    expect(within(nav).getByRole("link", { name: "Ringkasan" })).toHaveAttribute("href", "/");
    expect(within(nav).getByRole("link", { name: "Kapal" })).toHaveAttribute("href", "/ships");
  });

  it("halaman yang sedang dibuka ditandai aria-current", () => {
    renderBar({}, "/ships");
    expect(screen.getByRole("link", { name: "Kapal" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Ringkasan" })).not.toHaveAttribute("aria-current");
  });

  it("detail kapal tetap menandai Kapal, bukan Ringkasan", () => {
    renderBar({}, "/ships/abc");
    expect(screen.getByRole("link", { name: "Kapal" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Ringkasan" })).not.toHaveAttribute("aria-current");
  });

  it("pencarian meneruskan ketikan", async () => {
    const { onSearch } = renderBar();
    await userEvent.setup().type(screen.getByRole("searchbox"), "a");
    expect(onSearch).toHaveBeenCalledWith("a");
  });

  it("menampilkan jumlah online dan offline", () => {
    renderBar();
    const pulse = screen.getByLabelText("Ringkasan status armada");
    expect(pulse).toHaveTextContent("1 online");
    expect(pulse).toHaveTextContent("8 offline");
  });

  it("perlu perhatian hanya tampil bila ada", () => {
    renderBar({ attentionCount: 0 });
    expect(screen.queryByText(/perlu perhatian/)).not.toBeInTheDocument();
  });

  it("perlu perhatian tampil dengan jumlahnya", () => {
    renderBar({ attentionCount: 3 });
    expect(screen.getByLabelText("Ringkasan status armada")).toHaveTextContent(
      "3 perlu perhatian",
    );
  });

  it("tombol keluar tersedia", () => {
    renderBar();
    expect(screen.getByRole("button", { name: "Keluar" })).toBeInTheDocument();
  });
});
