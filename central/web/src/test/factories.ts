import type { FleetShip } from "@/lib/types";

/** Kapal dengan nilai wajar; setiap test menimpa yang relevan saja. */
export function makeShip(over: Partial<FleetShip> = {}): FleetShip {
  const now = new Date().toISOString();
  return {
    ship_id: "11111111-1111-1111-1111-111111111111",
    name: "KM Sinar Jaya",
    slug: "SHIP-023",
    imo_number: null,
    is_active: true,
    connection_state: "online",
    last_batch_received_at: now,
    last_telemetry_timestamp: now,
    last_contiguous_sequence: 1000,
    has_gap: false,
    pending_estimate: 0,
    agent_version: "0.1.0",
    config_version: "cfg-1",
    total_records: 5000,
    ...over,
  };
}

export function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}
