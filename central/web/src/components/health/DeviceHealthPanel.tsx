import { EmptyState, Field, Panel } from "@/components/ui/Primitives";
import { formatDuration, relativeTime } from "@/lib/format";
import type { EdgeHealth } from "@/lib/types";
import { StorageMeter } from "./StorageMeter";

/** Heartbeat dikirim tiap 60 detik; tiga kali terlewat berarti ada yang salah,
 *  satu yang hilang hanya jaringan yang sedang berkedip. */
const HEARTBEAT_STALE_SECONDS = 180;

function HealthBadge({ healthy }: { healthy: boolean }) {
  return (
    <span className={`badge badge--${healthy ? "ok" : "crit"}`}>
      <span className="badge__glyph" aria-hidden="true">
        {healthy ? "●" : "▲"}
      </span>
      {healthy ? "Sehat" : "Bermasalah"}
    </span>
  );
}

/** Nilai yang tidak terbaca dinyatakan terus terang. Meter kosong atau 0%
 *  akan terlihat seperti perangkat menganggur, padahal kita tidak tahu. */
function OptionalMeter({ label, percent }: { label: string; percent: number | null }) {
  if (percent === null) {
    return (
      <div className="meter">
        <div className="meter__head">
          <span className="meter__label">{label}</span>
          <span className="meter__value">Tidak tersedia</span>
        </div>
      </div>
    );
  }
  return <StorageMeter label={label} percent={percent} />;
}

function heartbeatAgeSeconds(iso: string | null, now: number): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  return Number.isNaN(then) ? null : Math.max(0, (now - then) / 1000);
}

export function DeviceHealthPanel({
  health,
  lastHeartbeatAt,
  now = Date.now(),
}: {
  health: EdgeHealth | null;
  lastHeartbeatAt: string | null;
  now?: number;
}) {
  if (!health) {
    return (
      <Panel>
        <EmptyState
          title="Belum ada heartbeat dari kapal ini"
          hint="Kapal belum melaporkan kondisi perangkatnya. Telemetry tetap bisa masuk tanpa heartbeat."
        />
      </Panel>
    );
  }

  const age = heartbeatAgeSeconds(lastHeartbeatAt, now);
  const late = age !== null && age > HEARTBEAT_STALE_SECONDS;
  const skew = health.clock_skew_seconds;

  return (
    <Panel>
      <div className="meters">
        <StorageMeter label="Disk" percent={health.disk_used_percent} />
        <OptionalMeter label="Memori" percent={health.memory_used_percent} />
        <OptionalMeter label="CPU" percent={health.cpu_used_percent} />
      </div>
      <dl className="fields">
        <Field label="Heartbeat terakhir">
          <span className="mono">{relativeTime(lastHeartbeatAt, now)}</span>{" "}
          {late && (
            <span className="badge badge--warn">
              <span className="badge__glyph" aria-hidden="true">
                ▲
              </span>
              Terlambat
            </span>
          )}
        </Field>
        <Field label="Uptime perangkat">
          <span className="mono">{formatDuration(health.uptime_seconds)}</span>
        </Field>
        <Field label="Akuisisi data">
          <HealthBadge healthy={health.collector_healthy} />
        </Field>
        <Field label="Penyimpanan lokal">
          <HealthBadge healthy={health.storage_healthy} />
        </Field>
        <Field label="Sambungan perangkat lapangan">
          <HealthBadge healthy={health.protocol_link_healthy} />
        </Field>
        <Field label="Selisih jam">
          <span className="mono">
            {skew === null ? "Tidak diketahui" : `${skew > 0 ? "+" : ""}${skew.toFixed(1).replace(".", ",")} dtk`}
          </span>
        </Field>
      </dl>
    </Panel>
  );
}
