import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OnboardShipDialog } from "./OnboardShipDialog";

const HASIL = {
  ship_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  slug: "SHIP-071",
  name: "KM Sinar Jaya",
  device_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  client_id: "ship-071",
  client_secret: "RAHASIA-SANGAT-PANJANG-123",
};

function renderDialog(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <OnboardShipDialog onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status < 400,
      status,
      json: async () => body,
    })),
  );
}

async function isiDanKirim() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/Nama kapal/), "KM Sinar Jaya");
  await user.type(screen.getByLabelText(/Kode kapal/), "SHIP-071");
  await user.click(screen.getByRole("button", { name: "Daftarkan kapal" }));
  return user;
}

beforeEach(() => {
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: vi.fn(async () => {}) } });
});
afterEach(() => vi.unstubAllGlobals());

describe("OnboardShipDialog", () => {
  it("mengirim formulir dan menampilkan kredensial", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    await isiDanKirim();

    await waitFor(() => expect(screen.getByText(/terdaftar/)).toBeInTheDocument());
    expect(screen.getByText(/FLEETVIEW_SYNC__DEVICE_SECRET=RAHASIA/)).toBeInTheDocument();
  });

  it("memperingatkan bahwa rahasia hanya tampil sekali", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    await isiDanKirim();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/hanya ditampilkan sekali/i),
    );
  });

  it("menahan tombol Selesai sampai kredensial diakui tersalin", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    const user = await isiDanKirim();

    const selesai = await screen.findByRole("button", { name: "Selesai" });
    expect(selesai).toBeDisabled();

    await user.click(screen.getByRole("checkbox"));
    expect(selesai).toBeEnabled();
  });

  it("menyalin ship_id dan device_id untuk edge.yaml, bukan hanya rahasianya", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    await isiDanKirim();

    await waitFor(() => expect(screen.getByText(/ship_id/)).toBeInTheDocument());
    expect(screen.getByText(new RegExp(HASIL.device_id))).toBeInTheDocument();
  });

  it("menampilkan pesan server saat slug bentrok", async () => {
    mockFetch(400, {
      ok: false,
      data: null,
      error: {
        code: "fleet.slug_taken",
        message: "slug 'SHIP-071' sudah dipakai kapal lain",
        details: {},
        retryable: false,
      },
      meta: {},
    });
    renderDialog();
    await isiDanKirim();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/sudah dipakai kapal lain/),
    );
  });

  it("menjelaskan kekurangan izin alih-alih pesan mentah", async () => {
    mockFetch(403, {
      ok: false,
      data: null,
      error: { code: "auth.forbidden", message: "butuh peran", details: {}, retryable: false },
      meta: {},
    });
    renderDialog();
    await isiDanKirim();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/tidak punya izin/i),
    );
  });

  it("bisa dibatalkan sebelum ada kredensial", async () => {
    const onClose = renderDialog();
    await userEvent.setup().click(screen.getByRole("button", { name: "Batal" }));
    expect(onClose).toHaveBeenCalled();
  });
});
