import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HostStoragePanel } from "./HostStoragePanel";
import type { HostStorage } from "@/lib/types";

const GB = 1024 ** 3;

function storage(over: Partial<HostStorage> = {}): HostStorage {
  return {
    disk: {
      path: "/",
      total_bytes: 500 * GB,
      used_bytes: 200 * GB,
      free_bytes: 300 * GB,
      used_percent: 40,
      level: "ok",
    },
    postgres_bytes: 5 * 1024 ** 2,
    ...over,
  };
}

describe("HostStoragePanel", () => {
  it("menampilkan pemakaian disk sebagai meter", () => {
    render(<HostStoragePanel storage={storage()} />);
    expect(screen.getByRole("meter", { name: "Disk host" })).toHaveAttribute("aria-valuenow", "40");
    expect(screen.getByText(/300 GB bebas dari 500 GB/)).toBeInTheDocument();
  });

  it("menyebut path yang diperiksa supaya tidak dikira partisi data", () => {
    render(<HostStoragePanel storage={storage()} />);
    expect(screen.getByText("/")).toBeInTheDocument();
  });

  it("mengikuti level dari server, bukan menghitung ambang sendiri", () => {
    const base = storage();
    render(
      <HostStoragePanel storage={storage({ disk: { ...base.disk!, used_percent: 85, level: "warning" } })} />,
    );
    expect(screen.getByText("Perlu perhatian")).toBeInTheDocument();
  });

  it("menampilkan ukuran database Postgres", () => {
    render(<HostStoragePanel storage={storage()} />);
    expect(screen.getByText("5,0 MB")).toBeInTheDocument();
  });

  it("disk tidak terbaca dinyatakan terus terang", () => {
    render(<HostStoragePanel storage={storage({ disk: null })} />);
    expect(screen.getByText("Pemakaian disk tidak dapat dibaca")).toBeInTheDocument();
    expect(screen.queryByRole("meter")).toBeNull();
  });

  it("ukuran Postgres tidak diketahui ditampilkan sebagai strip", () => {
    render(<HostStoragePanel storage={storage({ postgres_bytes: null })} />);
    expect(screen.getByText("Ukuran database Postgres").nextSibling).toHaveTextContent("—");
  });
});
