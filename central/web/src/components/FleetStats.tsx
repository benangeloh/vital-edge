import { boardHeadline } from "@/lib/narrative";
import {
  STATUS_MEANING,
  STATUS_META,
  STATUS_ORDER,
  type FleetSummary,
  type ShipStatus,
} from "@/lib/status";

/** Ringkasan armada: satu kalimat jawaban, lalu satu pita angka yang juga
 *  berfungsi sebagai penyaring daftar, dengan garis proporsi di dasarnya.
 *
 *  Pita, bukan deretan kartu: angka dan persentase sama terbacanya untuk 7
 *  maupun 700 kapal, dan hanya angkanya yang tampil. Arti tiap status ada di
 *  tooltip, bukan di setiap sel. */
export interface FleetStatsProps {
  summary: FleetSummary;
  selected: ShipStatus | "all";
  onSelect: (status: ShipStatus | "all") => void;
}

function percent(count: number, total: number): number {
  return total > 0 ? Math.round((count / total) * 100) : 0;
}

export function FleetStats({ summary, selected, onSelect }: FleetStatsProps) {
  const headline = boardHeadline(summary);
  const total = Math.max(1, summary.total);
  const segments = STATUS_ORDER.map((status) => ({
    status,
    count: summary[status],
    meta: STATUS_META[status],
  })).filter((s) => s.count > 0);

  return (
    <section className="stats" aria-label="Ringkasan status armada">
      <h2 className="stats__headline">
        <span className="tabular">{headline.count}</span> {headline.rest}
      </h2>
      <p className="stats__note">{headline.note}</p>

      <div className="stats__panel">
        <div className="stats__strip" role="group" aria-label="Saring daftar kapal menurut status">
          <button
            type="button"
            className={`stat stat--total${selected === "all" ? " stat--on" : ""}`}
            aria-pressed={selected === "all"}
            title="Semua kapal terdaftar"
            onClick={() => onSelect("all")}
          >
            <span className="stat__label">Total kapal</span>
            <span className="stat__count tabular">{summary.total}</span>
            <span className="stat__share">Semua</span>
          </button>

          {STATUS_ORDER.map((status) => {
            const meta = STATUS_META[status];
            const active = selected === status;
            return (
              <button
                key={status}
                type="button"
                className={`stat stat--${meta.tone}${active ? " stat--on" : ""}`}
                aria-pressed={active}
                title={STATUS_MEANING[status]}
                onClick={() => onSelect(active ? "all" : status)}
              >
                <span className="stat__label">
                  <span className="stat__dot" aria-hidden="true" />
                  {meta.label}
                </span>
                <span className="stat__count tabular">{summary[status]}</span>
                <span className="stat__share tabular">{percent(summary[status], summary.total)}%</span>
              </button>
            );
          })}
        </div>

        <div
          className="perf__bar"
          role="img"
          aria-label={
            segments.length > 0
              ? segments.map((s) => `${s.count} ${s.meta.label}`).join(", ")
              : "Belum ada kapal"
          }
        >
          {segments.map((s) => (
            <span
              key={s.status}
              className={`perf__seg perf__seg--${s.meta.tone}`}
              style={{ width: `${(s.count / total) * 100}%` }}
            />
          ))}
        </div>
      </div>
    </section>
  );
}
