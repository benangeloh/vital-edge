import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DecommissionShipDialog } from "./DecommissionShipDialog";
import { callOf, fail, mockFetchSequence, ok } from "@/test/fetch";
import { makeShip } from "@/test/factories";

const SHIP = makeShip({
  ship_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  name: "KM Sinar Jaya",
  slug: "SHIP-071",
});

function renderDialog(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DecommissionShipDialog ship={SHIP} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

const tombol = () => screen.getByRole("button", { name: "Nonaktifkan kapal" });

afterEach(() => vi.unstubAllGlobals());

describe("DecommissionShipDialog", () => {
  it("menjelaskan akibatnya: akses dicabut, data tetap tersimpan", () => {
    renderDialog();
    expect(screen.getByText(/akses kapal dicabut/i)).toBeInTheDocument();
    expect(screen.getByText(/data.*tetap tersimpan/i)).toBeInTheDocument();
  });

  it("menahan tombol sampai kode kapal diketik dengan benar", async () => {
    renderDialog();
    const user = userEvent.setup();
    expect(tombol()).toBeDisabled();

    await user.type(screen.getByLabelText(/Ketik SHIP-071/), "SHIP-07");
    expect(tombol()).toBeDisabled();

    await user.type(screen.getByLabelText(/Ketik SHIP-071/), "1");
    expect(tombol()).toBeEnabled();
  });

  it("huruf kecil pada kode kapal tetap diterima", async () => {
    renderDialog();
    await userEvent.setup().type(screen.getByLabelText(/Ketik SHIP-071/), "ship-071");
    expect(tombol()).toBeEnabled();
  });

  it("menonaktifkan kapal lalu menutup dialog", async () => {
    const fetchMock = mockFetchSequence([ok({ ship_id: SHIP.ship_id, revoked_credentials: ["ship-071"] })]);
    const onClose = renderDialog();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/Ketik SHIP-071/), "SHIP-071");
    await user.click(tombol());

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(callOf(fetchMock, 0)).toMatchObject({
      method: "POST",
      path: `/api/v1/ships/${SHIP.ship_id}/decommission`,
    });
  });

  it("tidak mengirim apa pun sebelum dikonfirmasi", () => {
    const fetchMock = mockFetchSequence([ok({})]);
    renderDialog();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("menampilkan kegagalan dan tetap terbuka", async () => {
    mockFetchSequence([fail(403, "auth.forbidden", "butuh peran")]);
    const onClose = renderDialog();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/Ketik SHIP-071/), "SHIP-071");
    await user.click(tombol());

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/tidak punya izin/i));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("bisa dibatalkan", async () => {
    const onClose = renderDialog();
    await userEvent.setup().click(screen.getByRole("button", { name: "Batal" }));
    expect(onClose).toHaveBeenCalled();
  });
});
