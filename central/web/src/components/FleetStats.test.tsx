import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FleetStats } from "./FleetStats";
import type { FleetSummary } from "@/lib/status";

const summary: FleetSummary = {
  total: 7, online: 0, syncing: 1, degraded: 0, offline: 6, inactive: 0, pendingRecords: 0,
};

function renderStats(over: Partial<React.ComponentProps<typeof FleetStats>> = {}) {
  const onSelect = vi.fn();
  render(<FleetStats summary={summary} selected="all" onSelect={onSelect} {...over} />);
  return { onSelect };
}

describe("FleetStats", () => {
  it("satu kartu untuk total dan satu untuk tiap status", () => {
    renderStats();
    for (const name of ["Total kapal", "Online", "Sinkron", "Terganggu", "Offline", "Nonaktif"]) {
      expect(screen.getByRole("button", { name: new RegExp(name) })).toBeInTheDocument();
    }
  });

  it("menampilkan jumlah dan persentase dari total", () => {
    renderStats();
    const sinkron = screen.getByRole("button", { name: /Sinkron/ });
    expect(within(sinkron).getByText("1")).toBeInTheDocument();
    expect(within(sinkron).getByText("14% dari total")).toBeInTheDocument();
    const offline = screen.getByRole("button", { name: /Offline/ });
    expect(within(offline).getByText("86% dari total")).toBeInTheDocument();
  });

  it("setiap kartu status menjelaskan artinya", () => {
    renderStats();
    const offline = screen.getByRole("button", { name: /Offline/ });
    expect(within(offline).getByText(/lebih dari satu jam/i)).toBeInTheDocument();
  });

  it("menekan kartu menyaring daftar, menekan lagi membatalkan", async () => {
    const { onSelect } = renderStats();
    await userEvent.click(screen.getByRole("button", { name: /Offline/ }));
    expect(onSelect).toHaveBeenCalledWith("offline");
  });

  it("kartu yang aktif ditandai dan menekannya mengembalikan ke semua", async () => {
    const { onSelect } = renderStats({ selected: "offline" });
    const offline = screen.getByRole("button", { name: /Offline/ });
    expect(offline).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(offline);
    expect(onSelect).toHaveBeenCalledWith("all");
  });

  it("kartu total menghapus saringan", async () => {
    const { onSelect } = renderStats({ selected: "offline" });
    await userEvent.click(screen.getByRole("button", { name: /Total kapal/ }));
    expect(onSelect).toHaveBeenCalledWith("all");
  });

  it("panel performa menjawab dalam satu kalimat dan batangnya berlabel teks", () => {
    renderStats();
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
      "1 dari 7 kapal sedang mengirim data",
    );
    const bar = screen.getByRole("img");
    expect(bar).toHaveAttribute("aria-label", expect.stringContaining("6 Offline"));
  });

  it("armada kosong tidak menghasilkan NaN", () => {
    const { container } = render(
      <FleetStats
        summary={{ total: 0, online: 0, syncing: 0, degraded: 0, offline: 0, inactive: 0, pendingRecords: 0 }}
        selected="all"
        onSelect={() => {}}
      />,
    );
    expect(container.textContent).not.toContain("NaN");
    expect(screen.getAllByText("0% dari total").length).toBeGreaterThan(0);
  });
});
