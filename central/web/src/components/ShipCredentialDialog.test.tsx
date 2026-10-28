import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShipCredentialDialog } from "./ShipCredentialDialog";
import { callOf, fail, mockFetchSequence, ok } from "@/test/fetch";
import { makeShip } from "@/test/factories";

const SHIP = makeShip({ ship_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", slug: "SHIP-071" });

const HANDOVER = {
  ship_id: SHIP.ship_id,
  slug: "SHIP-071",
  name: "KM Sinar Jaya",
  device_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  client_id: "ship-071",
  client_secret: "RAHASIA-YANG-SAMA-123",
  recoverable: true,
};

function renderDialog(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ShipCredentialDialog ship={SHIP} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

afterEach(() => vi.unstubAllGlobals());

describe("ShipCredentialDialog", () => {
  it("membuka kredensial kapal yang dipilih dan menawarkan skrip pasang", async () => {
    const fetchMock = mockFetchSequence([ok(HANDOVER)]);
    renderDialog();

    expect(await screen.findByRole("button", { name: /Unduh skrip pemasangan/ })).toBeInTheDocument();
    expect(callOf(fetchMock, 0)).toMatchObject({
      method: "GET",
      path: `/api/v1/ships/${SHIP.ship_id}/credential`,
    });
  });

  it("menampilkan rahasia yang sama dan tidak menyebutnya hanya tampil sekali", async () => {
    mockFetchSequence([ok(HANDOVER)]);
    renderDialog();

    await screen.findByText(/FLEETVIEW_SYNC__DEVICE_SECRET=RAHASIA-YANG-SAMA-123/);
    expect(screen.queryByText(/hanya ditampilkan sekali/i)).not.toBeInTheDocument();
  });

  it("bisa ditutup tanpa harus mencentang apa pun", async () => {
    mockFetchSequence([ok(HANDOVER)]);
    const onClose = renderDialog();

    // Tampilan memuat juga punya tombol Tutup; tunggu kredensialnya tampil dulu.
    await screen.findByRole("button", { name: /Unduh skrip pemasangan/ });
    const tutup = screen.getByRole("button", { name: "Tutup" });
    expect(tutup).toBeEnabled();
    await userEvent.setup().click(tutup);
    expect(onClose).toHaveBeenCalled();
  });

  it("menawarkan penerbitan ulang bila rahasia lama tidak tersimpan", async () => {
    mockFetchSequence([
      fail(400, "fleet.credential_not_recoverable", "rahasia kredensial ini tidak tersimpan"),
    ]);
    renderDialog();

    expect(await screen.findByText(/tidak tersimpan/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Terbitkan kredensial baru/ })).toBeInTheDocument();
  });

  it("menerbitkan kredensial baru lalu menampilkannya", async () => {
    const fetchMock = mockFetchSequence([
      fail(400, "fleet.credential_not_recoverable", "rahasia kredensial ini tidak tersimpan"),
      ok({ ...HANDOVER, client_id: "baru-1", client_secret: "RAHASIA-BARU-999" }, 201),
    ]);
    renderDialog();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Terbitkan kredensial baru/ }));

    expect(await screen.findByText(/RAHASIA-BARU-999/)).toBeInTheDocument();
    expect(callOf(fetchMock, 1)).toMatchObject({
      method: "POST",
      path: `/api/v1/ships/${SHIP.ship_id}/credential`,
    });
  });

  it("menjelaskan bahwa kredensial lama tidak otomatis dicabut", async () => {
    mockFetchSequence([
      fail(400, "fleet.credential_not_recoverable", "rahasia kredensial ini tidak tersimpan"),
    ]);
    renderDialog();
    expect(await screen.findByText(/tidak otomatis dicabut/i)).toBeInTheDocument();
  });

  it("menawarkan penerbitan ulang juga bila belum ada kredensial aktif", async () => {
    mockFetchSequence([
      fail(400, "fleet.no_active_credential", "kapal ini tidak punya kredensial aktif"),
    ]);
    renderDialog();
    expect(await screen.findByRole("button", { name: /Terbitkan kredensial baru/ })).toBeInTheDocument();
  });

  it("menjelaskan kekurangan izin alih-alih pesan mentah", async () => {
    mockFetchSequence([fail(403, "auth.forbidden", "butuh peran")]);
    renderDialog();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/tidak punya izin/i));
    expect(screen.queryByRole("button", { name: /Terbitkan/ })).not.toBeInTheDocument();
  });

  it("menampilkan pesan server untuk kegagalan lain, tanpa tawaran penerbitan ulang", async () => {
    mockFetchSequence([fail(400, "fleet.ship_not_found", "kapal tidak ditemukan")]);
    renderDialog();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/kapal tidak ditemukan/));
    expect(screen.queryByRole("button", { name: /Terbitkan/ })).not.toBeInTheDocument();
  });
});
