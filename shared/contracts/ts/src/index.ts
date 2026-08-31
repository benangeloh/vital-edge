/**
 * Tipe TypeScript untuk format wire FleetView.
 *
 * Sumber kebenarannya adalah model Pydantic di `contracts/python`; JSON Schema
 * di `contracts/schemas` dihasilkan darinya. Berkas ini ditulis manual agar
 * tetap terbaca, dan CI memeriksa bahwa schema hasil generasi tidak basi.
 *
 * Dashboard hanya butuh tipe untuk baca — ia tidak pernah membuat batch.
 */

export const SCHEMA_VERSION = "1.0" as const;

export type Quality = "good" | "stale" | "suspect" | "substituted";

export type AcquisitionSource = "live" | "file_import" | "simulated" | "manual";

export type Transport = "lan" | "wifi" | "cellular" | "usb";

export type BatchStatus = "staging" | "committed" | "rejected";

export type ConnectionState = "online" | "degraded" | "offline";

/** Nilai field yang boleh masuk ke InfluxDB. Bukan hanya number — ada input
 *  digital (boolean), counter (integer), dan barcode (string). */
export type FieldValue = number | boolean | string;

export interface Reading {
  seq: number;
  /** Epoch mikrodetik UTC menurut jam perangkat. Bukan patokan urutan — `seq` yang patokan. */
  ts: number;
  sensor_id: string;
  measurement: string;
  /** Sensor skalar memakai kunci tunggal `value`; GPS memakai lat/lon/sog/cog. */
  fields: Record<string, FieldValue>;
  unit: string | null;
  quality: Quality;
  source: AcquisitionSource;
  tags: Record<string, string>;
}

export interface Ack {
  batch_id: string;
  status: BatchStatus;
  last_contiguous_sequence: number;
  server_received_at: string;
  record_count: number;
  message: string | null;
}

export interface SyncState {
  ship_id: string;
  last_contiguous_sequence: number;
  /** Bila lebih besar dari last_contiguous_sequence, ada celah yang belum tertutup. */
  highest_sequence_seen: number;
  committed_batch_ids: string[];
  server_time: string;
}

export interface EdgeHealth {
  disk_used_percent: number;
  memory_used_percent: number;
  cpu_used_percent: number;
  uptime_seconds: number;
  collector_healthy: boolean;
  storage_healthy: boolean;
  protocol_link_healthy: boolean;
  clock_skew_seconds: number | null;
}

/** Envelope response yang dipakai seragam di seluruh API. */
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
