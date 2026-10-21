import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SensorPicker, type SensorOption } from "./SensorPicker";

const recent = new Date(Date.now() - 20_000).toISOString();
const old = new Date(Date.now() - 3 * 3600_000).toISOString();

const sensors: SensorOption[] = [
  { sensor_id: "me_rpm", metric: "rpm", unit: "rpm", last_seen_at: recent },
  { sensor_id: "fw_temp", metric: "temperature", unit: "degC", last_seen_at: recent },
  { sensor_id: "exhaust_temp", metric: "temperature", unit: "degC", last_seen_at: old },
  { sensor_id: "lo_press", metric: "pressure", unit: "bar", last_seen_at: null },
];

function renderPicker(value = "") {
  const onChange = vi.fn();
  render(<SensorPicker sensors={sensors} value={value} onChange={onChange} />);
  return { onChange };
}

const open = () => userEvent.click(screen.getByRole("combobox", { name: /Sensor/ }));

describe("SensorPicker", () => {
  it("tertutup menampilkan pilihan saat ini", () => {
    renderPicker("fw_temp");
    const trigger = screen.getByRole("combobox", { name: /Sensor/ });
    expect(trigger).toHaveTextContent("fw_temp");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("tanpa pilihan menampilkan Semua sensor", () => {
    renderPicker();
    expect(screen.getByRole("combobox", { name: /Sensor/ })).toHaveTextContent("Semua sensor");
  });

  it("dikelompokkan menurut jenis ukuran dengan nama yang dipahami", async () => {
    renderPicker();
    await open();
    const list = screen.getByRole("listbox");
    for (const group of ["Putaran mesin", "Suhu", "Tekanan"]) {
      expect(within(list).getByText(group)).toBeInTheDocument();
    }
    expect(within(list).getAllByRole("option")).toHaveLength(5);
  });

  it("menandai sensor yang sedang aktif dan yang sudah lama diam", async () => {
    renderPicker();
    await open();
    const aktif = screen.getByRole("option", { name: /me_rpm/ });
    expect(within(aktif).getByText("Aktif")).toBeInTheDocument();
    const lama = screen.getByRole("option", { name: /exhaust_temp/ });
    expect(within(lama).getByText(/3 jam lalu/)).toBeInTheDocument();
    const belum = screen.getByRole("option", { name: /lo_press/ });
    expect(within(belum).getByText("Belum ada data")).toBeInTheDocument();
  });

  it("memilih sensor memanggil onChange dan menutup daftar", async () => {
    const { onChange } = renderPicker();
    await open();
    await userEvent.click(screen.getByRole("option", { name: /fw_temp/ }));
    expect(onChange).toHaveBeenCalledWith("fw_temp");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("Semua sensor mengosongkan saringan", async () => {
    const { onChange } = renderPicker("fw_temp");
    await open();
    await userEvent.click(screen.getByRole("option", { name: /Semua sensor/ }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("pilihan saat ini ditandai", async () => {
    renderPicker("fw_temp");
    await open();
    expect(screen.getByRole("option", { name: /fw_temp/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: /me_rpm/ })).toHaveAttribute("aria-selected", "false");
  });

  it("pencarian menyaring menurut id, jenis, maupun satuan", async () => {
    renderPicker();
    await open();
    const search = screen.getByRole("searchbox", { name: "Cari sensor" });
    await userEvent.type(search, "suhu");
    expect(screen.getAllByRole("option")).toHaveLength(2);
    await userEvent.clear(search);
    await userEvent.type(search, "bar");
    expect(screen.getByRole("option", { name: /lo_press/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /me_rpm/ })).not.toBeInTheDocument();
  });

  it("tidak ada yang cocok dijelaskan", async () => {
    renderPicker();
    await open();
    await userEvent.type(screen.getByRole("searchbox", { name: "Cari sensor" }), "zzz");
    expect(screen.getByText(/Tidak ada sensor yang cocok/)).toBeInTheDocument();
  });

  it("bisa dikendalikan dengan papan ketik: panah, Enter, Escape", async () => {
    const { onChange } = renderPicker();
    await open();
    const search = screen.getByRole("searchbox", { name: "Cari sensor" });
    await userEvent.type(search, "{ArrowDown}{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0]![0]).not.toBe("");

    await open();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /Sensor/ })).toHaveFocus();
  });
});
