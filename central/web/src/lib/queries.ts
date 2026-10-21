import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, auth } from "./api";
import { EXPORT_MAX_ROWS, resolveRange, type LoggerRange } from "./logger";
import type {
  FleetShip,
  ReadingRow,
  ReadingsResponse,
  SensorRow,
  ShipDetail,
  SyncStatus,
  TelemetryResponse,
} from "./types";

/** Dashboard operasional: data harus segar tanpa operator perlu menekan apa pun. */
const LIVE_REFETCH_MS = 10_000;

export function useFleet(search?: string) {
  return useQuery({
    queryKey: ["fleet", search ?? ""],
    queryFn: async () => {
      const query = search ? `?q=${encodeURIComponent(search)}&limit=500` : "?limit=500";
      const { data } = await api.get<FleetShip[]>(`/api/v1/ships${query}`);
      return data;
    },
    refetchInterval: LIVE_REFETCH_MS,
    // Data lama tetap ditampilkan saat menyegar, supaya tabel tidak berkedip
    // kosong tiap sepuluh detik.
    placeholderData: (previous) => previous,
  });
}

export function useShip(shipId: string | undefined) {
  return useQuery({
    queryKey: ["ship", shipId],
    queryFn: async () => (await api.get<ShipDetail>(`/api/v1/ships/${shipId}`)).data,
    enabled: Boolean(shipId),
  });
}

export function useShipSensors(shipId: string | undefined) {
  return useQuery({
    queryKey: ["sensors", shipId],
    queryFn: async () => (await api.get<SensorRow[]>(`/api/v1/ships/${shipId}/sensors`)).data,
    enabled: Boolean(shipId),
  });
}

export function useSyncStatus(shipId: string | undefined) {
  return useQuery({
    queryKey: ["sync", shipId],
    queryFn: async () => (await api.get<SyncStatus>(`/api/v1/sync/ships/${shipId}`)).data,
    enabled: Boolean(shipId),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

const rangeKey = (range: LoggerRange) =>
  range.presetId ?? `${range.start.toISOString()}/${range.stop.toISOString()}`;

function rangeParams(shipId: string, range: LoggerRange, sensorId?: string): URLSearchParams {
  const { start, stop } = resolveRange(range);
  const params = new URLSearchParams({
    ship_id: shipId,
    start: start.toISOString(),
    stop: stop.toISOString(),
  });
  if (sensorId) params.set("sensor_id", sensorId);
  return params;
}

/** Grafik. Mengikuti rentang yang sama dengan tabel logger. */
export function useTelemetry(shipId: string | undefined, range: LoggerRange, sensorId?: string) {
  return useQuery({
    queryKey: ["telemetry", shipId, rangeKey(range), sensorId ?? ""],
    queryFn: async () => {
      const params = rangeParams(String(shipId), range, sensorId);
      return (await api.get<TelemetryResponse>(`/api/v1/telemetry/series?${params}`)).data;
    },
    enabled: Boolean(shipId),
    refetchInterval: range.presetId ? LIVE_REFETCH_MS : false,
    placeholderData: (previous) => previous,
  });
}

export interface ReadingsQuery {
  range: LoggerRange;
  sensorId?: string;
  offset: number;
  limit: number;
}

/** Tabel logger: pembacaan mentah, terbaru dulu. */
export function useReadings(shipId: string | undefined, q: ReadingsQuery) {
  return useQuery({
    queryKey: ["readings", shipId, rangeKey(q.range), q.sensorId ?? "", q.offset, q.limit],
    queryFn: async () => {
      const params = rangeParams(String(shipId), q.range, q.sensorId);
      params.set("limit", String(q.limit));
      params.set("offset", String(q.offset));
      return (await api.get<ReadingsResponse>(`/api/v1/telemetry/readings?${params}`)).data;
    },
    enabled: Boolean(shipId),
    // Hanya halaman pertama dari rentang "sampai sekarang" yang ikut bergerak.
    refetchInterval: q.range.presetId && q.offset === 0 ? LIVE_REFETCH_MS : false,
    placeholderData: (previous) => previous,
  });
}

const EXPORT_PAGE = 1000;

/** Seluruh baris untuk diunduh, dibaca per halaman sampai habis atau mencapai batas. */
export async function fetchReadingsForExport(
  shipId: string,
  range: LoggerRange,
  sensorId?: string,
  maxRows = EXPORT_MAX_ROWS,
): Promise<{ rows: ReadingRow[]; truncated: boolean }> {
  const rows: ReadingRow[] = [];
  let offset = 0;
  while (rows.length < maxRows) {
    const params = rangeParams(shipId, range, sensorId);
    params.set("limit", String(Math.min(EXPORT_PAGE, maxRows - rows.length)));
    params.set("offset", String(offset));
    const { data } = await api.get<ReadingsResponse>(`/api/v1/telemetry/readings?${params}`);
    rows.push(...data.rows);
    if (!data.has_more) return { rows, truncated: false };
    offset += data.rows.length;
  }
  return { rows, truncated: true };
}

export interface OnboardResult {
  ship_id: string;
  slug: string;
  name: string;
  device_id: string;
  client_id: string;
  /** Hanya dikembalikan sekali, saat penerbitan. Tidak pernah bisa dibaca ulang. */
  client_secret: string;
}

export interface OnboardInput {
  name: string;
  slug: string;
  imo_number?: string;
  device_name?: string;
  hardware?: string;
}

export function useOnboardShip() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: OnboardInput) =>
      (await api.post<OnboardResult>("/api/v1/ships/onboard", input)).data,
    onSuccess: () => client.invalidateQueries({ queryKey: ["fleet"] }),
  });
}

export function useLogin(onAuthenticated?: () => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: { username: string; password: string }) => {
      const { data } = await api.post<{ access_token: string }>("/api/v1/auth/login", body);
      return data;
    },
    onSuccess: (data) => {
      auth.set(data.access_token);
      client.invalidateQueries();
      onAuthenticated?.();
    },
  });
}
