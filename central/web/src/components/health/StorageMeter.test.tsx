import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StorageMeter, levelForPercent } from "./StorageMeter";

describe("levelForPercent", () => {
  it.each([
    [0, "ok"],
    [79.9, "ok"],
    [80, "warn"],
    [89.9, "warn"],
    [90, "crit"],
    [100, "crit"],
  ])("%s%% -> %s", (percent, level) => {
    expect(levelForPercent(percent)).toBe(level);
  });
});

describe("StorageMeter", () => {
  it("mengekspos nilainya sebagai meter yang bisa dibaca pembaca layar", () => {
    render(<StorageMeter label="Disk" percent={62.5} />);
    const meter = screen.getByRole("meter", { name: "Disk" });
    expect(meter).toHaveAttribute("aria-valuenow", "62.5");
    expect(meter).toHaveAttribute("aria-valuemin", "0");
    expect(meter).toHaveAttribute("aria-valuemax", "100");
  });

  it("tingkat tidak hanya bergantung warna — ada label teks", () => {
    render(<StorageMeter label="Disk" percent={93} />);
    expect(screen.getByText("Kritis")).toBeInTheDocument();
  });

  it("menampilkan persen dan keterangan tambahan", () => {
    render(<StorageMeter label="Disk" percent={42} detail="580 GB bebas" />);
    expect(screen.getByText("42,0%")).toBeInTheDocument();
    expect(screen.getByText(/580 GB bebas/)).toBeInTheDocument();
  });

  it("persen di luar rentang dijepit, bukan membuat batang meluap", () => {
    const { container } = render(<StorageMeter label="Disk" percent={140} />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "100");
    expect(container.querySelector<HTMLElement>(".meter__fill")?.style.width).toBe("100%");
  });

  it("level bisa ditentukan dari luar, mengikuti ambang server", () => {
    render(<StorageMeter label="Disk" percent={50} level="warn" />);
    expect(screen.getByText("Perlu perhatian")).toBeInTheDocument();
  });
});
