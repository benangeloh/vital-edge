import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DeviceHealthPanel } from "./DeviceHealthPanel";
import type { EdgeHealth } from "@/lib/types";

const NOW = new Date("2026-10-19T03:00:00Z").getTime();

function health(over: Partial<EdgeHealth> = {}): EdgeHealth {
  return {
    disk_used_percent: 62.5,
    memory_used_percent: 40,
    cpu_used_percent: 12,
    uptime_seconds: 3 * 3600 + 5 * 60,
    collector_healthy: true,
    storage_healthy: true,
    protocol_link_healthy: true,
    clock_skew_seconds: 0.4,
    ...over,
  };
}

describe("DeviceHealthPanel", () => {
  it("tanpa heartbeat, tidak mengarang angka", () => {
    render(<DeviceHealthPanel health={null} lastHeartbeatAt={null} now={NOW} />);
    expect(screen.getByText("Belum ada heartbeat dari kapal ini")).toBeInTheDocument();
    expect(screen.queryByRole("meter")).toBeNull();
  });

  it("menampilkan disk, memori, dan CPU sebagai meter", () => {
    render(<DeviceHealthPanel health={health()} lastHeartbeatAt="2026-10-19T02:59:30Z" now={NOW} />);
    expect(screen.getByRole("meter", { name: "Disk" })).toHaveAttribute("aria-valuenow", "62.5");
    expect(screen.getByRole("meter", { name: "Memori" })).toHaveAttribute("aria-valuenow", "40");
    expect(screen.getByRole("meter", { name: "CPU" })).toHaveAttribute("aria-valuenow", "12");
  });

  it("disk hampir penuh ditandai dengan teks, bukan warna saja", () => {
    render(
      <DeviceHealthPanel
        health={health({ disk_used_percent: 93 })}
        lastHeartbeatAt="2026-10-19T02:59:30Z"
        now={NOW}
      />,
    );
    expect(screen.getByText("Kritis")).toBeInTheDocument();
  });

  it("menampilkan uptime dan umur heartbeat terakhir", () => {
    render(<DeviceHealthPanel health={health()} lastHeartbeatAt="2026-10-19T02:59:30Z" now={NOW} />);
    expect(screen.getByText("3 jam 5 mnt")).toBeInTheDocument();
    expect(screen.getByText("30 dtk")).toBeInTheDocument();
  });

  it("heartbeat yang terlambat diberi peringatan", () => {
    render(<DeviceHealthPanel health={health()} lastHeartbeatAt="2026-10-19T02:50:00Z" now={NOW} />);
    expect(screen.getByText("Terlambat")).toBeInTheDocument();
  });

  it("heartbeat segar tidak diberi peringatan", () => {
    render(<DeviceHealthPanel health={health()} lastHeartbeatAt="2026-10-19T02:59:30Z" now={NOW} />);
    expect(screen.queryByText("Terlambat")).toBeNull();
  });

  it("komponen yang bermasalah terlihat jelas", () => {
    render(
      <DeviceHealthPanel
        health={health({ collector_healthy: false, protocol_link_healthy: false })}
        lastHeartbeatAt="2026-10-19T02:59:30Z"
        now={NOW}
      />,
    );
    expect(screen.getAllByText("Bermasalah")).toHaveLength(2);
    expect(screen.getAllByText("Sehat")).toHaveLength(1);
  });

  it("CPU dan memori yang tidak terbaca dinyatakan tidak tersedia, bukan nol", () => {
    render(
      <DeviceHealthPanel
        health={health({ cpu_used_percent: null, memory_used_percent: null })}
        lastHeartbeatAt="2026-10-19T02:59:30Z"
        now={NOW}
      />,
    );
    expect(screen.getByRole("meter", { name: "Disk" })).toBeInTheDocument();
    expect(screen.queryByRole("meter", { name: "CPU" })).toBeNull();
    expect(screen.queryByRole("meter", { name: "Memori" })).toBeNull();
    expect(screen.getByText("CPU").parentElement).toHaveTextContent("Tidak tersedia");
    expect(screen.getByText("Memori").parentElement).toHaveTextContent("Tidak tersedia");
  });

  it("uptime yang tidak terbaca ditampilkan sebagai strip, bukan 0 dtk", () => {
    render(
      <DeviceHealthPanel
        health={health({ uptime_seconds: null })}
        lastHeartbeatAt="2026-10-19T02:59:30Z"
        now={NOW}
      />,
    );
    expect(screen.getByText("Uptime perangkat").nextSibling).toHaveTextContent("—");
    expect(screen.queryByText("0 dtk")).toBeNull();
  });

  it("selisih jam tidak diketahui ditampilkan sebagai strip", () => {
    render(
      <DeviceHealthPanel
        health={health({ clock_skew_seconds: null })}
        lastHeartbeatAt="2026-10-19T02:59:30Z"
        now={NOW}
      />,
    );
    expect(screen.getByText("Selisih jam").nextSibling).toHaveTextContent("—");
  });
});
