import { SCHEMA_VERSION } from "@fleetview/contracts";

/**
 * Placeholder Phase 1.
 *
 * Dashboard armada yang sesungguhnya dibangun di Phase 6. Yang dibuktikan
 * berkas ini hanyalah bahwa build jalan dan tipe dari package contracts bisa
 * dipakai lintas batas Python/TypeScript.
 */
export default function App() {
  return (
    <main style={{ maxWidth: "40rem", margin: "0 auto", padding: "var(--space-6)" }}>
      <h1 style={{ fontSize: "var(--text-xl)", margin: 0 }}>FleetView</h1>
      <p style={{ color: "var(--color-text-muted)" }}>
        Fondasi Phase 1. Dashboard armada dibangun di Phase 6.
      </p>
      <p className="mono" style={{ fontSize: "var(--text-sm)" }}>
        schema_version {SCHEMA_VERSION}
      </p>
    </main>
  );
}
