/** Client API terhadap Central Platform. */

export interface ApiError {
  code: string;
  message: string;
  details: Record<string, unknown>;
  /** Klien memakai ini untuk memutuskan mengulang atau menyerah. */
  retryable: boolean;
}

export interface Envelope<T> {
  ok: boolean;
  data: T | null;
  error: ApiError | null;
  meta: Record<string, unknown>;
}

export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly error: ApiError,
  ) {
    super(error.message);
    this.name = "ApiFailure";
  }
}

const TOKEN_KEY = "fleetview.token";

export const auth = {
  get token(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      // Private browsing atau penyimpanan situs diblokir. Bukan alasan untuk
      // membuat aplikasi gagal total — pengguna cukup diminta login lagi.
      return null;
    }
  },
  set(token: string) {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* diabaikan sengaja */
    }
  },
  clear() {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* diabaikan sengaja */
    }
  },
};

export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<{ data: T; meta: Record<string, unknown> }> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = auth.token;
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(path, { ...init, headers });
  let body: Envelope<T> | null = null;
  try {
    body = (await response.json()) as Envelope<T>;
  } catch {
    body = null;
  }

  if (!response.ok || !body?.ok) {
    const error: ApiError = body?.error ?? {
      code: "network.unreachable",
      message: `Permintaan gagal (HTTP ${response.status})`,
      details: {},
      retryable: response.status >= 500,
    };
    if (response.status === 401) auth.clear();
    throw new ApiFailure(response.status, error);
  }

  return { data: body.data as T, meta: body.meta ?? {} };
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, payload?: unknown) =>
    request<T>(path, { method: "POST", body: payload ? JSON.stringify(payload) : undefined }),
  put: <T>(path: string, payload?: unknown) =>
    request<T>(path, { method: "PUT", body: payload ? JSON.stringify(payload) : undefined }),
};
