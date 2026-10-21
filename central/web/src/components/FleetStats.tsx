import { ShipIcon } from "@/components/shell/nav-icons";
import { StatusIcon } from "@/components/ui/StatusIcon";
import { boardHeadline } from "@/lib/narrative";
import {
  STATUS_MEANING,
  STATUS_META,
  STATUS_ORDER,
  type FleetSummary,
  type ShipStatus,
} from "@/lib/status";

/** Ringkasan armada: kartu status yang juga berfungsi sebagai penyaring daftar,
 *  dan satu batang proporsi untuk "seberapa sehat armada ini".
 *
 *  Kartu, bukan deretan kotak per kapal: dengan 100 kapal kotak-kotak itu
 *  menutupi halaman, sedangkan angka dan persentase tetap sama terbacanya
 *  untuk 7 maupun 700 kapal. */
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
      <div className="stats__cards" role="group" aria-label="Saring daftar kapal menurut status">
        <button
          type="button"
          className={`stat stat--total${selected === "all" ? " stat--on" : ""}`}
          aria-pressed={selected === "all"}
          onClick={() => onSelect("all")}
        >
          <span className="stat__top">
            <span className="stat__icon">
              <ShipIcon className="stat__svg" />
            </span>
            <span className="stat__label">Total kapal</span>
          </span>
          <span className="stat__count tabular">{summary.total}</span>
          <span className="stat__foot">Semua kapal terdaftar</span>
        </button>

        {STATUS_ORDER.map((status) => {
          const meta = STATUS_META[status];
          const share = percent(summary[status], summary.total);
          const active = selected === status;
          return (
            <button
              key={status}
              type="button"
              className={`stat stat--${meta.tone}${active ? " stat--on" : ""}`}
              aria-pressed={active}
              onClick={() => onSelect(active ? "all" : status)}
            >
              <span className="stat__top">
                <span className="stat__icon">
                  <StatusIcon status={status} className="stat__svg" />
                </span>
                <span className="stat__label">{meta.label}</span>
              </span>
              <span className="stat__count tabular">{summary[status]}</span>
              <span className="stat__share tabular">{share}% dari total</span>
              <span className="stat__meter" aria-hidden="true">
                <span className="stat__fill" style={{ width: `${share}%` }} />
              </span>
              <span className="stat__meaning">{STATUS_MEANING[status]}</span>
            </button>
          );
        })}
      </div>

      <div className="perf">
        <h2 className="perf__headline">
          <span className="perf__count tabular">{headline.count}</span> {headline.rest}
        </h2>
        <p className="perf__note">{headline.note}</p>
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
        <ul className="perf__legend">
          {STATUS_ORDER.map((status) => (
            <li key={status} className="perf__item">
              <span
                className={`perf__key perf__key--${STATUS_META[status].tone}`}
                aria-hidden="true"
              />
              <span>{STATUS_META[status].label}</span>
              <strong className="tabular">{summary[status]}</strong>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
