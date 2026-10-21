import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DateRangeBar, type LoggerRange } from "./DateRangeBar";
import { PRESETS } from "@/lib/logger";

const value: LoggerRange = {
  presetId: "24h",
  start: new Date(2026, 9, 20, 8, 0),
  stop: new Date(2026, 9, 21, 8, 0),
};

function renderBar(over: Partial<React.ComponentProps<typeof DateRangeBar>> = {}) {
  const onChange = vi.fn();
  render(<DateRangeBar value={value} onChange={onChange} {...over} />);
  return { onChange };
}

describe("DateRangeBar", () => {
  it("menampilkan semua pilihan cepat dan menandai yang aktif", () => {
    renderBar();
    for (const p of PRESETS) {
      expect(screen.getByRole("button", { name: p.label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "24 jam" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "1 jam" })).toHaveAttribute("aria-pressed", "false");
  });

  it("memilih pilihan cepat mengirim rentang yang berakhir sekarang", async () => {
    const { onChange } = renderBar();
    await userEvent.click(screen.getByRole("button", { name: "7 hari" }));
    const next = onChange.mock.calls[0]![0] as LoggerRange;
    expect(next.presetId).toBe("7d");
    expect(next.stop.getTime() - next.start.getTime()).toBe(7 * 86_400_000);
    expect(Math.abs(Date.now() - next.stop.getTime())).toBeLessThan(5000);
  });

  it("isian tanggal terisi dari rentang yang aktif", () => {
    renderBar();
    expect(screen.getByLabelText("Dari")).toHaveValue("2026-10-20T08:00");
    expect(screen.getByLabelText("Sampai")).toHaveValue("2026-10-21T08:00");
  });

  it("menerapkan rentang tanggal sendiri", async () => {
    const { onChange } = renderBar();
    const dari = screen.getByLabelText("Dari");
    const sampai = screen.getByLabelText("Sampai");
    await userEvent.clear(dari);
    await userEvent.type(dari, "2026-10-01T00:00");
    await userEvent.clear(sampai);
    await userEvent.type(sampai, "2026-10-05T12:30");
    await userEvent.click(screen.getByRole("button", { name: "Tampilkan" }));
    const next = onChange.mock.calls[0]![0] as LoggerRange;
    expect(next.presetId).toBeNull();
    expect(next.start.getTime()).toBe(new Date(2026, 9, 1, 0, 0).getTime());
    expect(next.stop.getTime()).toBe(new Date(2026, 9, 5, 12, 30).getTime());
  });

  it("rentang terbalik dijelaskan dan tidak diterapkan", async () => {
    const { onChange } = renderBar();
    const dari = screen.getByLabelText("Dari");
    await userEvent.clear(dari);
    await userEvent.type(dari, "2026-10-25T00:00");
    await userEvent.click(screen.getByRole("button", { name: "Tampilkan" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Tanggal akhir harus setelah tanggal awal");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("isian dikaitkan dengan pesan galatnya untuk pembaca layar", async () => {
    renderBar();
    const dari = screen.getByLabelText("Dari");
    await userEvent.clear(dari);
    await userEvent.click(screen.getByRole("button", { name: "Tampilkan" }));
    expect(screen.getByLabelText("Dari")).toHaveAttribute("aria-invalid", "true");
  });
});
