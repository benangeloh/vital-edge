import type { FleetSummary } from "@/lib/status";
import { STATUS_META, type ShipStatus } from "@/lib/status";

/** Komposisi armada sebagai satu batang proporsional.
 *
 * Sengaja bukan lima kartu berjejer. Lima kartu memaksa mata membandingkan lima
 * angka; satu batang menunjukkan proporsinya seketika — "sebagian besar hijau,
 * ada sedikit abu-abu" terbaca sebelum satu angka pun dibaca. Angkanya tetap ada
 * di bawah, untuk saat operator butuh nilai persisnya. */
const ORDER: ShipStatus[] = ["online", "syncing", "degraded", "offline", "inactive"];

export function FleetBar({ summary }: { summary: FleetSummary }) {
  const total = Math.max(1, summary.total);
  const segments = ORDER.map((status) => ({
    status,
    count: summary[status],
    meta: STATUS_META[status],
  })).filter((s) => s.count > 0);

  return (
    <div className="fleet-bar">
      <div className="fleet-bar__headline">
        <span className="fleet-bar__total tabular">{summary.total}</span>
        <span className="fleet-bar__unit">kapal dalam armada</span>
      </div>

      <div
        className="fleet-bar__track"
        role="img"
        aria-label={segments.map((s) => `${s.count} ${s.meta.label}`).join(", ")}
      >
        {segments.map((s) => (
          <span
            key={s.status}
            className={`fleet-bar__seg fleet-bar__seg--${s.meta.tone}`}
            style={{ width: `${(s.count / total) * 100}%` }}
          />
        ))}
      </div>

      <dl className="fleet-bar__legend">
        {ORDER.map((status) => {
          const meta = STATUS_META[status];
          return (
            <div key={status} className="fleet-bar__stat">
              <dt>
                <span
                  className={`fleet-bar__key fleet-bar__key--${meta.tone}`}
                  aria-hidden="true"
                />
                {meta.label}
              </dt>
              <dd className="tabular">{summary[status]}</dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}
