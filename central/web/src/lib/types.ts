/** Bentuk data dari Central Platform.
 *
 * Diturunkan dari OpenAPI di `/openapi.json`. Ditulis manual agar tetap terbaca;
 * yang menjaga keduanya tetap cocok adalah integration test di sisi backend. */

export type ConnectionState = "online" | "degraded" | "offline";

export interface FleetShip {
  ship_id: string;
  name: string;
  slug: string;
  imo_number: string | null;
  is_active: boolean;
  connection_state: ConnectionState;
  last_batch_received_at: string | null;
  last_telemetry_timestamp: string | null;
  last_contiguous_sequence: number;
  /** Ada rentang sequence yang belum lengkap — normal sesaat setelah impor USB. */
  has_gap: boolean;
  pending_estimate: number | null;
  agent_version: string | null;
  config_version: string | null;
  total_records: number;
}

export interface ShipDetail {
  ship_id: string;
  name: string;
  slug: string;
  imo_number: string | null;
  call_sign: string | null;
  is_active: boolean;
  devices: Array<{
    device_id: string;
    name: string;
    hardware: string | null;
    field_device: string | null;
    agent_version: string | null;
  }>;
  active_config_version: string | null;
}

export interface SensorRow {
  sensor_id: string;
  metric: string;
  unit: string | null;
  status: string;
  display_name: string | null;
  tags: Record<string, string>;
  last_seen_at: string | null;
}

/** Kondisi Edge Agent yang dilaporkan lewat heartbeat. */
export interface EdgeHealth {
  disk_used_percent: number;
  /** `null` bila perangkat tidak bisa membacanya (mis. bukan Linux); bukan nol. */
  memory_used_percent: number | null;
  cpu_used_percent: number | null;
  uptime_seconds: number | null;
  collector_healthy: boolean;
  storage_healthy: boolean;
  protocol_link_healthy: boolean;
  /** Positif = jam kapal mendahului server. `null` bila belum pernah tersambung. */
  clock_skew_seconds: number | null;
}

export interface SyncStatus {
  ship_id: string;
  last_contiguous_sequence: number;
  highest_sequence_seen: number;
  committed_batch_ids: string[];
  server_time: string;
  has_gap: boolean;
  total_batches: number;
  total_records: number;
  connection_state: ConnectionState;
  pending_estimate: number | null;
  last_batch_received_at: string | null;
  /** `null` selama kapal belum pernah mengirim heartbeat. */
  last_heartbeat_at: string | null;
  agent_version: string | null;
  active_transport: string | null;
  oldest_pending_age_seconds: number | null;
  edge_health: EdgeHealth | null;
}

export interface AlertEvent {
  id: string;
  ship_id: string;
  sensor_id: string | null;
  severity: "info" | "warning" | "critical";
  message: string;
  value: number | null;
  occurred_at: string;
  acknowledged_at: string | null;
}

export interface TelemetrySeries {
  sensor_id: string;
  measurement: string;
  field: string;
  unit: string | null;
  /** `[waktu ISO, nilai]`. */
  points: Array<[string, number | string]>;
}

export interface TelemetryResponse {
  series: TelemetrySeries[];
  bucket_used: string;
  window: string;
  aggregate: string;
}

export type ServerStorageLevel = "ok" | "warning" | "critical";

export interface DiskUsage {
  path: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  used_percent: number;
  level: ServerStorageLevel;
}

export interface HostStorage {
  /** `null` bila server tidak bisa membaca partisinya. */
  disk: DiskUsage | null;
  postgres_bytes: number | null;
}

export interface SystemHealth {
  status: "healthy" | "degraded";
  checks: Record<string, string>;
  storage?: HostStorage;
}
