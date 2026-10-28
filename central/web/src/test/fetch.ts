import { vi } from "vitest";

export interface FakeResponse {
  status: number;
  body: unknown;
}

export const ok = (data: unknown, status = 200): FakeResponse => ({
  status,
  body: { ok: true, data, error: null, meta: {} },
});

export const fail = (status: number, code: string, message: string): FakeResponse => ({
  status,
  body: { ok: false, data: null, error: { code, message, details: {}, retryable: false }, meta: {} },
});

/** Menjawab tiap panggilan fetch berurutan; panggilan terakhir diulang bila
 *  jumlah panggilan melebihi daftar. */
export function mockFetchSequence(responses: FakeResponse[]) {
  let i = 0;
  const fn = vi.fn(async () => {
    const r = responses[Math.min(i, responses.length - 1)]!;
    i += 1;
    return { ok: r.status < 400, status: r.status, json: async () => r.body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** [method, path, body] dari panggilan ke-n. */
export function callOf(
  fn: ReturnType<typeof vi.fn>,
  n: number,
): { method: string; path: string; body: unknown } {
  const [path, init] = fn.mock.calls[n] as [string, RequestInit | undefined];
  return {
    method: init?.method ?? "GET",
    path,
    body: init?.body ? JSON.parse(String(init.body)) : undefined,
  };
}
