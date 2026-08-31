import { useMemo, useState } from "react";
import { ShipTable } from "@/components/ShipTable";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState, ErrorState, Loading, Panel, Section } from "@/components/ui/Primitives";
import { absoluteTime, compactNumber, relativeTime } from "@/lib/format";
import { useAcknowledgeAlert, useAlerts, useFleet, useSystemHealth } from "@/lib/queries";
import { STATUS_META, shipStatus, summarise } from "@/lib/status";

export function ShipsPage({ search }: { search: string }) {
  const { data: ships, isLoading, error, refetch } = useFleet();
  const now = Date.now();

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return ships ?? [];
    return (ships ?? []).filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        s.slug.toLowerCase().includes(needle) ||
        s.ship_id.toLowerCase().includes(needle),
    );
  }, [ships, search]);

  if (isLoading) return <Loading label="Memuat kapal" />;
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Kapal</h1>
        <p className="page-sub">
          {filtered.length} dari {ships?.length ?? 0} kapal
          {search ? ` cocok dengan "${search}"` : ""}
        </p>
      </div>
      <Section title="Daftar kapal">
        <ShipTable ships={filtered} now={now} />
      </Section>
    </>
  );
}

export function AlertsPage() {
  const { data: alerts, isLoading, error } = useAlerts();
  const acknowledge = useAcknowledgeAlert();
  const [onlyOpen, setOnlyOpen] = useState(true);

  if (isLoading) return <Loading label="Memuat alert" />;
  if (error) return <ErrorState error={error} />;

  const list = (alerts ?? []).filter((a) => (onlyOpen ? !a.acknowledged_at : true));

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Alert</h1>
      </div>
      <Section
        title="Kejadian"
        actions={
          <button
            type="button"
            className={onlyOpen ? "chip chip--on" : "chip"}
            aria-pressed={onlyOpen}
            onClick={() => setOnlyOpen((v) => !v)}
          >
            Hanya yang belum diakui
          </button>
        }
      >
        {list.length === 0 ? (
          <EmptyState
            title="Tidak ada alert"
            hint="Evaluasi aturan alert dijadwalkan pada fase berikutnya; metadata dan API-nya sudah siap."
          />
        ) : (
          <Panel flush>
            <table className="table table--dense">
              <thead>
                <tr>
                  <th scope="col">Tingkat</th>
                  <th scope="col">Pesan</th>
                  <th scope="col">Sensor</th>
                  <th scope="col" className="num">Terjadi</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {list.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <span className={`badge badge--${a.severity === "critical" ? "crit" : "warn"}`}>
                        <span className="badge__glyph" aria-hidden="true">▲</span>
                        {a.severity}
                      </span>
                    </td>
                    <th scope="row">{a.message}</th>
                    <td className="mono muted">{a.sensor_id ?? "—"}</td>
                    <td className="num muted" title={absoluteTime(a.occurred_at)}>
                      {relativeTime(a.occurred_at)}
                    </td>
                    <td className="num">
                      {!a.acknowledged_at && (
                        <button
                          type="button"
                          className="btn btn--ghost"
                          onClick={() => acknowledge.mutate(a.id)}
                        >
                          Akui
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}
      </Section>
    </>
  );
}

export function SyncCenterPage() {
  const { data: ships, isLoading } = useFleet();
  const now = Date.now();
  if (isLoading) return <Loading />;

  const backlog = (ships ?? [])
    .filter((s) => (s.pending_estimate ?? 0) > 0 || s.has_gap)
    .sort((a, b) => (b.pending_estimate ?? 0) - (a.pending_estimate ?? 0));
  const summary = summarise(ships ?? [], now);

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Pusat Sinkronisasi</h1>
        <p className="page-sub">
          {compactNumber(summary.pendingRecords)} record tertunda di seluruh armada
        </p>
      </div>
      <Section title="Kapal dengan tunggakan" description="Diurutkan dari tunggakan terbesar">
        {backlog.length === 0 ? (
          <EmptyState title="Tidak ada tunggakan" hint="Semua kapal sudah tersetor." />
        ) : (
          <ShipTable ships={backlog} now={now} />
        )}
      </Section>
    </>
  );
}

export function DevicesPage() {
  const { data: ships, isLoading } = useFleet();
  const now = Date.now();
  if (isLoading) return <Loading />;

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Perangkat</h1>
      </div>
      <Section title="Edge Agent per kapal">
        <Panel flush>
          <table className="table table--dense">
            <thead>
              <tr>
                <th scope="col">Kapal</th>
                <th scope="col">Status</th>
                <th scope="col">Versi agent</th>
                <th scope="col">Versi config</th>
                <th scope="col" className="num">Kabar terakhir</th>
              </tr>
            </thead>
            <tbody>
              {(ships ?? []).map((s) => (
                <tr key={s.ship_id}>
                  <th scope="row">{s.name}</th>
                  <td>
                    <StatusBadge meta={STATUS_META[shipStatus(s, now)]} />
                  </td>
                  <td className="mono">{s.agent_version ?? "—"}</td>
                  <td className="mono muted">{s.config_version ?? "—"}</td>
                  <td className="num muted">{relativeTime(s.last_batch_received_at, now)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </Section>
    </>
  );
}

export function SystemHealthPage() {
  const { data, isLoading, error } = useSystemHealth();
  if (isLoading) return <Loading />;
  if (error) return <ErrorState error={error} />;

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Kesehatan Sistem</h1>
      </div>
      <Section title="Komponen platform">
        <Panel>
          <dl className="fields">
            {Object.entries(data?.checks ?? {}).map(([name, state]) => (
              <div key={name} className="field">
                <dt className="field__label">{name}</dt>
                <dd className="field__value">
                  <span className={`badge badge--${state === "ok" ? "ok" : "crit"}`}>
                    <span className="badge__glyph" aria-hidden="true">
                      {state === "ok" ? "●" : "▲"}
                    </span>
                    {state}
                  </span>
                </dd>
              </div>
            ))}
          </dl>
        </Panel>
      </Section>
    </>
  );
}

export function PlaceholderPage({ title, note }: { title: string; note: string }) {
  return (
    <>
      <div className="page-head">
        <h1 className="page-title">{title}</h1>
      </div>
      <Section title={title}>
        <EmptyState title="Belum tersedia" hint={note} />
      </Section>
    </>
  );
}
