import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, auth } from "./api";
import type {
  AlertEvent,
  FleetShip,
  SensorRow,
  ShipDetail,
  SyncStatus,
  SystemHealth,
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

export function useTelemetry(shipId: string | undefined, rangeSeconds: number, sensorId?: string) {
  return useQuery({
    queryKey: ["telemetry", shipId, rangeSeconds, sensorId ?? ""],
    queryFn: async () => {
      const params = new URLSearchParams({
        ship_id: String(shipId),
        range_seconds: String(rangeSeconds),
      });
      if (sensorId) params.set("sensor_id", sensorId);
      return (await api.get<TelemetryResponse>(`/api/v1/telemetry/series?${params}`)).data;
    },
    enabled: Boolean(shipId),
    placeholderData: (previous) => previous,
  });
}

export function useAlerts(shipId?: string) {
  return useQuery({
    queryKey: ["alerts", shipId ?? ""],
    queryFn: async () => {
      const query = shipId ? `?ship_id=${shipId}` : "";
      return (await api.get<AlertEvent[]>(`/api/v1/alerts${query}`)).data;
    },
    refetchInterval: LIVE_REFETCH_MS,
  });
}

export function useAcknowledgeAlert() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/alerts/${id}/acknowledge`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

export function useSystemHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => (await api.get<SystemHealth>("/api/v1/health")).data,
    refetchInterval: 30_000,
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
