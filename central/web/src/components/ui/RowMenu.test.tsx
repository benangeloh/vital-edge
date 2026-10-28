import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RowMenu } from "./RowMenu";

function renderMenu() {
  const items = [
    { label: "Edit data kapal", onSelect: vi.fn() },
    { label: "Kredensial & skrip pasang", onSelect: vi.fn() },
    { label: "Nonaktifkan kapal", onSelect: vi.fn(), danger: true },
  ];
  render(
    <div>
      <RowMenu label="Aksi lainnya untuk KM Dua" items={items} />
      <button type="button">di luar</button>
    </div>,
  );
  return items;
}

const trigger = () => screen.getByRole("button", { name: "Aksi lainnya untuk KM Dua" });

describe("RowMenu", () => {
  it("tertutup sampai pemicunya ditekan", () => {
    renderMenu();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(trigger()).toHaveAttribute("aria-haspopup", "menu");
  });

  it("menampilkan semua pilihan sebagai menuitem", async () => {
    renderMenu();
    await userEvent.setup().click(trigger());

    expect(trigger()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual([
      "Edit data kapal",
      "Kredensial & skrip pasang",
      "Nonaktifkan kapal",
    ]);
  });

  it("pilihan pertama langsung mendapat fokus supaya bisa dioperasikan keyboard", async () => {
    renderMenu();
    await userEvent.setup().click(trigger());
    expect(screen.getByRole("menuitem", { name: "Edit data kapal" })).toHaveFocus();
  });

  it("memilih menjalankan aksinya lalu menutup menu", async () => {
    const items = renderMenu();
    const user = userEvent.setup();
    await user.click(trigger());
    await user.click(screen.getByRole("menuitem", { name: "Kredensial & skrip pasang" }));

    expect(items[1]!.onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("Escape menutup dan mengembalikan fokus ke pemicu", async () => {
    renderMenu();
    const user = userEvent.setup();
    await user.click(trigger());
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();
  });

  it("klik di luar menutup menu tanpa menjalankan apa pun", async () => {
    const items = renderMenu();
    const user = userEvent.setup();
    await user.click(trigger());
    await user.click(screen.getByRole("button", { name: "di luar" }));

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    for (const item of items) expect(item.onSelect).not.toHaveBeenCalled();
  });

  it("panah atas dan bawah berpindah antar pilihan, berputar di ujung", async () => {
    renderMenu();
    const user = userEvent.setup();
    await user.click(trigger());

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Kredensial & skrip pasang" })).toHaveFocus();
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Edit data kapal" })).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Nonaktifkan kapal" })).toHaveFocus();
  });

  it("pilihan berbahaya diberi tanda dan dipisah dari yang lain", async () => {
    renderMenu();
    await userEvent.setup().click(trigger());

    expect(screen.getByRole("menuitem", { name: "Nonaktifkan kapal" })).toHaveClass(
      "rowmenu__item--danger",
    );
    expect(screen.getByRole("separator")).toBeInTheDocument();
  });

  it("tetap terbuka saat halaman menggulir, misalnya karena baris digulir ke area terlihat", async () => {
    renderMenu();
    await userEvent.setup().click(trigger());

    fireEvent.scroll(window);
    fireEvent.scroll(document.body);
    fireEvent(window, new Event("resize"));

    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("menekan pemicu lagi menutup menu", async () => {
    renderMenu();
    const user = userEvent.setup();
    await user.click(trigger());
    await user.click(trigger());
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
