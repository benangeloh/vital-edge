import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EditShipDialog } from "./EditShipDialog";
import { callOf, fail, mockFetchSequence, ok } from "@/test/fetch";
import { makeShip } from "@/test/factories";

const SHIP = makeShip({
  ship_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  name: "KM Sinar Jaya",
  slug: "SHIP-071",
  imo_number: "IMO9074729",
});

function renderDialog(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <EditShipDialog ship={SHIP} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

afterEach(() => vi.unstubAllGlobals());

describe("EditShipDialog", () => {
  it("terisi dengan data kapal saat ini", () => {
    renderDialog();
    expect(screen.getByLabelText(/Nama kapal/)).toHaveValue("KM Sinar Jaya");
    expect(screen.getByLabelText(/Nomor IMO/)).toHaveValue("IMO9074729");
  });

  it("kode kapal ditampilkan tetapi tidak bisa diubah", () => {
    renderDialog();
    const kode = screen.getByLabelText(/Kode kapal/);
    expect(kode).toHaveValue("SHIP-071");
    expect(kode).toBeDisabled();
  });

  it("mengirim perubahan lalu menutup dialog", async () => {
    const fetchMock = mockFetchSequence([ok({ ship_id: SHIP.ship_id })]);
    const onClose = renderDialog();
    const user = userEvent.setup();

    const nama = screen.getByLabelText(/Nama kapal/);
    await user.clear(nama);
    await user.type(nama, "KM Nama Baru");
    await user.click(screen.getByRole("button", { name: "Simpan" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(callOf(fetchMock, 0)).toEqual({
      method: "PATCH",
      path: `/api/v1/ships/${SHIP.ship_id}`,
      body: { name: "KM Nama Baru", imo_number: "IMO9074729" },
    });
  });

  it("nomor IMO yang dikosongkan dikirim sebagai kosong", async () => {
    const fetchMock = mockFetchSequence([ok({ ship_id: SHIP.ship_id })]);
    renderDialog();
    const user = userEvent.setup();

    await user.clear(screen.getByLabelText(/Nomor IMO/));
    await user.click(screen.getByRole("button", { name: "Simpan" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(callOf(fetchMock, 0).body).toEqual({ name: "KM Sinar Jaya", imo_number: "" });
  });

  it("menampilkan pesan server saat nomor IMO bentrok dan tetap terbuka", async () => {
    mockFetchSequence([fail(400, "fleet.imo_taken", "nomor IMO 'IMO1' sudah dipakai kapal lain")]);
    const onClose = renderDialog();

    await userEvent.setup().click(screen.getByRole("button", { name: "Simpan" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/sudah dipakai kapal lain/));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("menjelaskan kekurangan izin", async () => {
    mockFetchSequence([fail(403, "auth.forbidden", "butuh peran")]);
    renderDialog();
    await userEvent.setup().click(screen.getByRole("button", { name: "Simpan" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/tidak punya izin/i));
  });

  it("bisa dibatalkan", async () => {
    const onClose = renderDialog();
    await userEvent.setup().click(screen.getByRole("button", { name: "Batal" }));
    expect(onClose).toHaveBeenCalled();
  });
});
