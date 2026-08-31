import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FleetBar } from "@/components/FleetBar";
import { ShipTable } from "@/components/ShipTable";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ErrorState, Loading, Section } from "@/components/ui/Primitives";
import { relativeTime } from "@/lib/format";
import { useFleet } from "@/lib/queries";
import { STATUS_META, needsAttention, shipStatus, summarise, type ShipStatus } from "@/lib/status";

const STATUS_FILTERS: Array<{ value: ShipStatus | "all"; label: string }> = [
  { value: "all", label: "Semua" },
  { value: "online", label: "Online" },
  { value: "syncing", label: "Sinkron" },
  { value: "degraded", label: "Terganggu" },
  { value: "offline", label: "Offline" },
];

export function FleetOverview({ search }: { search: string }) {
  const { data: ships, isLoading, error, refetch } = useFleet();
  const [status, setStatus] = useState<ShipStatus | "all">("all");
  const now = Date.now();

  const summary = useMemo(() => summarise(ships ?? [], now), [ships, now]);
  const attention = useMemo(() => needsAttention(ships ?? [], now), [ships, now]);

  const filtered = useMemo(() => {
    let list = ships ?? [];
    if (status !== "all") list = list.filter((s) => shipStatus(s, now) === status);
    if (search.trim()) {
      const needle = search.trim().toLowerCase();
      list = list.filter(
        (s) =>
          s.name.toLowerCase().includes(needle) ||
          s.slug.toLowerCase().includes(needle) ||
          s.ship_id.toLowerCase().includes(needle),
      );
    }
    return list;
  }, [ships, status, search, now]);

  if (isLoading) return <Loading label="Memuat armada" />;
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Ikhtisar Armada</h1>
        <p className="page-sub">
          Diperbarui otomatis tiap 10 detik · {relativeTime(new Date(now).toISOString(), now)} lalu
        </p>
      </div>

      <FleetBar summary={summary} />

      {/* Panel ini hanya muncul kalau memang ada yang bermasalah. Panel
          "semua baik" yang selalu tampil justru melatih mata mengabaikannya. */}
      {attention.length > 0 && (
        <Section
          title="Perlu perhatian"
          description={`${attention.length} kapal tidak dalam kondisi normal, diurutkan dari yang paling gawat`}
        >
          <ul className="attention">
            {attention.slice(0, 6).map((ship) => {
              const meta = STATUS_META[shipStatus(ship, now)];
              return (
                <li key={ship.ship_id} className="attention__row">
                  <StatusBadge meta={meta} />
                  <Link to={`/ships/${ship.ship_id}`} className="attention__name">
                    {ship.name}
                  </Link>
                  <span className="attention__detail">
                    data terakhir {relativeTime(ship.last_telemetry_timestamp, now)} lalu
                    {ship.pending_estimate
                      ? ` · ${ship.pending_estimate.toLocaleString("id-ID")} tertunda`
                      : ""}
                    {ship.has_gap ? " · ada celah sequence" : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        </Section>
      )}

      <Section
        title="Semua kapal"
        actions={
          <div className="filters" role="group" aria-label="Saring berdasarkan status">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                className={status === f.value ? "chip chip--on" : "chip"}
                aria-pressed={status === f.value}
                onClick={() => setStatus(f.value)}
              >
                {f.label}
              </button>
            ))}
          </div>
        }
      >
        <ShipTable ships={filtered} now={now} />
      </Section>
    </>
  );
}
