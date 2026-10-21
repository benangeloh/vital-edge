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

function renderDialog(onClose = vi.fn(), existingSlugs: string[] = []) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <OnboardShipDialog onClose={onClose} existingSlugs={existingSlugs} />
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
  const kode = screen.getByLabelText(/Kode kapal/);
  await user.clear(kode);
  await user.type(kode, "SHIP-071");
  await user.click(screen.getByRole("button", { name: "Daftarkan kapal" }));
  return user;
}

beforeEach(() => {
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: vi.fn(async () => {}) } });
});
afterEach(() => vi.unstubAllGlobals());

describe("OnboardShipDialog", () => {
  it("mengisi kode kapal dengan nomor berikutnya yang masih kosong", () => {
    renderDialog(vi.fn(), ["SHIP-001", "SHIP-048"]);
    expect(screen.getByLabelText(/Kode kapal/)).toHaveValue("SHIP-049");
  });

  it("hanya meminta nama dan kode kapal", () => {
    renderDialog();
    expect(screen.getByLabelText(/Nama kapal/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Kode kapal/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/IMO/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Nama perangkat/)).not.toBeInTheDocument();
    expect(screen.queryByText(/opsional/i)).not.toBeInTheDocument();
  });

  it("bahasanya netral terhadap jenis komputer di kapal", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    await isiDanKirim();
    await screen.findByText(/Pasang otomatis/);
    // Nama sistem operasi boleh disebut; yang tidak boleh adalah menyiratkan
    // bahwa kapal pasti memakai Raspberry Pi.
    expect(document.body.textContent).not.toMatch(/raspberry pi (kapal|di kapal)|ip-pi|edge pi/i);
  });

  it("hanya mengirim nama dan kode ke server", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    await isiDanKirim();
    await screen.findByText(/terdaftar/);
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body));
    expect(body).toEqual({ name: "KM Sinar Jaya", slug: "SHIP-071" });
  });

  it("setelah terdaftar menawarkan skrip pemasangan otomatis", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    await isiDanKirim();
    expect(await screen.findByRole("button", { name: /Unduh skrip pemasangan/ })).toBeInTheDocument();
    expect(screen.getByText(/sudo bash pasang-fleetview-SHIP-071\.sh/)).toBeInTheDocument();
    expect(screen.getByText(/Windows belum didukung/)).toBeInTheDocument();
  });

  it("alamat pusat terisi dari alamat dashboard dan diperingatkan bila hanya dikenal lokal", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    await isiDanKirim();
    const field = await screen.findByLabelText(/Alamat pusat/);
    expect(field).toHaveValue(window.location.origin);
    expect(screen.getByRole("status")).toHaveTextContent(/hanya dikenal di komputer ini/i);
  });

  it("peringatan hilang setelah alamat pusat diganti dengan alamat yang bisa dijangkau", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    const user = await isiDanKirim();
    const field = await screen.findByLabelText(/Alamat pusat/);
    await user.clear(field);
    await user.type(field, "https://fleetview.spil.example");
    expect(screen.queryByText(/hanya dikenal di komputer ini/i)).not.toBeInTheDocument();
  });

  it("mengunduh skrip berisi kredensial, alamat pusat, dan alamat panel yang diisi", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    let blob: Blob | null = null;
    let fileName = "";
    // jsdom tidak punya createObjectURL, jadi dipasang langsung.
    URL.createObjectURL = (b: Blob | MediaSource) => {
      blob = b as Blob;
      return "blob:fake";
    };
    URL.revokeObjectURL = () => {};
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      fileName = this.download;
    });

    renderDialog();
    const user = await isiDanKirim();
    const pusat = await screen.findByLabelText(/Alamat pusat/);
    await user.clear(pusat);
    await user.type(pusat, "https://fleetview.spil.example");
    await user.type(screen.getByLabelText(/Alamat IP panel/), "192.168.100.100");
    await user.click(screen.getByRole("button", { name: /Unduh skrip pemasangan/ }));

    expect(fileName).toBe("pasang-fleetview-SHIP-071.sh");
    const text = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(blob!);
    });
    expect(text).toContain("CENTRAL_URL='https://fleetview.spil.example'");
    expect(text).toContain("CLIENT_ID='ship-071'");
    expect(text).toContain("SECRET='RAHASIA-SANGAT-PANJANG-123'");
    expect(text).toContain("PANEL_HOST='192.168.100.100'");
    // Mengunduh dianggap sudah menyimpan kredensial.
    expect(screen.getByRole("button", { name: "Selesai" })).toBeEnabled();
  });

  it("cara manual tetap tersedia, dilipat di bawah skrip", async () => {
    mockFetch(201, { ok: true, data: HASIL, error: null, meta: {} });
    renderDialog();
    await isiDanKirim();
    const summary = await screen.findByText(/Cara manual/);
    expect(summary.closest("details")).not.toBeNull();
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
