import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState, ErrorState, Field, Loading, Panel, Section } from "@/components/ui/Primitives";
import { absoluteTime, compactNumber, relativeTime, shortId } from "@/lib/format";
import { useShip, useShipSensors, useSyncStatus, useTelemetry } from "@/lib/queries";
import { STATUS_META, shipStatus } from "@/lib/status";

const TABS = [
  "Ikhtisar",
  "Telemetry Langsung",
  "Telemetry Historis",
  "Status Sync",
  "Kesehatan Perangkat",
  "Alert",
  "Konfigurasi",
] as const;

const RANGES = [
  { label: "1j", seconds: 3600 },
  { label: "6j", seconds: 21_600 },
  { label: "24j", seconds: 86_400 },
  { label: "7h", seconds: 604_800 },
  { label: "30h", seconds: 2_592_000 },
];

/** Empat besaran yang paling sering dilihat operator. Warnanya berbeda per
 *  besaran dan konsisten di seluruh aplikasi, sehingga bentuk kurva bisa
 *  dikenali tanpa membaca judulnya. */
const PRIMARY_METRICS = [
  { metric: "rpm", label: "RPM", color: "var(--green-primary)" },
  { metric: "fuel_level", label: "Bahan Bakar", color: "var(--status-sync)" },
  { metric: "pressure", label: "Tekanan", color: "var(--status-warn)" },
  { metric: "temperature", label: "Suhu", color: "var(--red-brand)" },
];

export function ShipDetail() {
  const { shipId } = useParams<{ shipId: string }>();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Ikhtisar");
  const [range, setRange] = useState(RANGES[2]!);

  const { data: ship, isLoading, error } = useShip(shipId);
  const { data: sync } = useSyncStatus(shipId);
  const { data: sensors } = useShipSensors(shipId);
  const { data: telemetry } = useTelemetry(shipId, range.seconds);

  if (isLoading) return <Loading label="Memuat kapal" />;
  if (error) return <ErrorState error={error} />;
  if (!ship) return <EmptyState title="Kapal tidak ditemukan" />;

  const seriesFor = (metric: string) =>
    telemetry?.series.find((s) => s.measurement === metric && s.field === "value");

  return (
    <>
      <div className="page-head">
        <nav className="crumbs" aria-label="Remah roti">
          <Link to="/">Armada</Link>
          <span aria-hidden="true">/</span>
          <span>{ship.name}</span>
        </nav>
        <div className="page-head__row">
          <h1 className="page-title">{ship.name}</h1>
          {sync && (
            /* Status diturunkan dengan fungsi yang SAMA dengan tabel armada.
               Memakai connection_state mentah dari server membuat halaman ini
               menampilkan "Online" untuk kapal yang di tabel tertulis offline —
               dua jawaban berbeda untuk pertanyaan yang sama. */
            <StatusBadge
              meta={
                STATUS_META[
                  shipStatus({
                    ship_id: ship.ship_id,
                    name: ship.name,
                    slug: ship.slug,
                    imo_number: ship.imo_number,
                    is_active: ship.is_active,
                    connection_state: sync.connection_state,
                    last_batch_received_at: sync.last_batch_received_at,
                    last_telemetry_timestamp: sync.last_batch_received_at,
                    last_contiguous_sequence: sync.last_contiguous_sequence,
                    has_gap: sync.has_gap,
                    pending_estimate: sync.pending_estimate,
                    agent_version: null,
                    config_version: null,
                    total_records: sync.total_records,
                  })
                ]
              }
            />
          )}
        </div>
        <p className="page-sub mono">
          {ship.slug} · {shortId(ship.ship_id, 13)}
          {ship.imo_number ? ` · IMO ${ship.imo_number}` : ""}
        </p>
      </div>

      <div className="tabs" role="tablist" aria-label="Bagian detail kapal">
        {TABS.map((name) => (
          <button
            key={name}
            role="tab"
            type="button"
            aria-selected={tab === name}
            className={tab === name ? "tab tab--on" : "tab"}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </div>

      <div role="tabpanel" className="tabpanel">
        {(tab === "Ikhtisar" || tab === "Telemetry Langsung" || tab === "Telemetry Historis") && (
          <Section
            title={tab === "Ikhtisar" ? "Telemetry utama" : tab}
            actions={
              <div className="filters" role="group" aria-label="Rentang waktu">
                {RANGES.map((r) => (
                  <button
                    key={r.label}
                    type="button"
                    className={range.seconds === r.seconds ? "chip chip--on" : "chip"}
                    aria-pressed={range.seconds === r.seconds}
                    onClick={() => setRange(r)}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            }
          >
            <div className="charts">
              {PRIMARY_METRICS.map((m) => {
                const series = seriesFor(m.metric);
                const last = series?.points.at(-1)?.[1];
                return (
                  <Panel key={m.metric}>
                    <div className="chart__head">
                      <h3 className="chart__title">{m.label}</h3>
                      <span className="chart__value tabular">
                        {typeof last === "number" ? last.toFixed(1) : "—"}
                        <span className="chart__unit">{series?.unit ?? ""}</span>
                      </span>
                    </div>
                    <TimeSeriesChart series={series} color={m.color} label={m.label} />
                  </Panel>
                );
              })}
            </div>
            {telemetry?.bucket_used && telemetry.bucket_used !== "raw" && (
              <p className="note">
                Ditampilkan sebagai agregat {telemetry.window} ({telemetry.aggregate}), bukan data
                mentah.
              </p>
            )}
          </Section>
        )}

        {tab === "Ikhtisar" && (
          <Section title="Identitas">
            <Panel>
              <dl className="fields">
                <Field label="Nama">{ship.name}</Field>
                <Field label="Slug">
                  <span className="mono">{ship.slug}</span>
                </Field>
                <Field label="IMO">{ship.imo_number ?? "—"}</Field>
                <Field label="Call sign">{ship.call_sign ?? "—"}</Field>
                <Field label="Versi konfigurasi">
                  <span className="mono">{ship.active_config_version ?? "—"}</span>
                </Field>
                <Field label="Sensor terdaftar">{sensors?.length ?? 0}</Field>
              </dl>
            </Panel>
          </Section>
        )}

        {tab === "Status Sync" && (
          <Section title="Status sinkronisasi">
            <Panel>
              {sync ? (
                <dl className="fields">
                  <Field label="Watermark contiguous">
                    <span className="tabular">{sync.last_contiguous_sequence.toLocaleString("id-ID")}</span>
                  </Field>
                  <Field label="Sequence tertinggi">
                    <span className="tabular">{sync.highest_sequence_seen.toLocaleString("id-ID")}</span>
                  </Field>
                  <Field label="Celah">
                    {sync.has_gap ? (
                      <span className="gap-flag">ada — sebagian rentang belum lengkap</span>
                    ) : (
                      "tidak ada"
                    )}
                  </Field>
                  <Field label="Total batch">
                    <span className="tabular">{compactNumber(sync.total_batches)}</span>
                  </Field>
                  <Field label="Total record">
                    <span className="tabular">{compactNumber(sync.total_records)}</span>
                  </Field>
                  <Field label="Tertunda di kapal">
                    <span className="tabular">{compactNumber(sync.pending_estimate)}</span>
                  </Field>
                </dl>
              ) : (
                <EmptyState title="Belum ada data sinkronisasi" />
              )}
            </Panel>
          </Section>
        )}

        {tab === "Kesehatan Perangkat" && (
          <Section title="Perangkat">
            <Panel flush>
              <table className="table table--dense">
                <thead>
                  <tr>
                    <th scope="col">Perangkat</th>
                    <th scope="col">Hardware</th>
                    <th scope="col">Perangkat lapangan</th>
                    <th scope="col">Versi agent</th>
                  </tr>
                </thead>
                <tbody>
                  {ship.devices.map((d) => (
                    <tr key={d.device_id}>
                      <th scope="row">{d.name}</th>
                      <td className="muted">{d.hardware ?? "—"}</td>
                      <td className="muted">{d.field_device ?? "—"}</td>
                      <td className="mono">{d.agent_version ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {ship.devices.length === 0 && <EmptyState title="Belum ada perangkat terdaftar" />}
            </Panel>
          </Section>
        )}

        {tab === "Konfigurasi" && (
          <Section
            title="Sensor terdaftar"
            description="Sensor yang belum dikenal didaftarkan otomatis saat pertama terlihat"
          >
            <Panel flush>
              <table className="table table--dense">
                <thead>
                  <tr>
                    <th scope="col">Sensor</th>
                    <th scope="col">Besaran</th>
                    <th scope="col">Satuan</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">Terlihat</th>
                  </tr>
                </thead>
                <tbody>
                  {(sensors ?? []).map((s) => (
                    <tr key={s.sensor_id}>
                      <th scope="row" className="mono">{s.sensor_id}</th>
                      <td>{s.metric}</td>
                      <td className="muted">{s.unit ?? "—"}</td>
                      <td className="muted">{s.status}</td>
                      <td className="num muted" title={absoluteTime(s.last_seen_at)}>
                        {relativeTime(s.last_seen_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(sensors ?? []).length === 0 && <EmptyState title="Belum ada sensor terlihat" />}
            </Panel>
          </Section>
        )}

        {tab === "Alert" && (
          <Section title="Alert kapal ini">
            <EmptyState
              title="Belum ada alert"
              hint="Evaluasi aturan alert dijadwalkan pada fase berikutnya."
            />
          </Section>
        )}
      </div>
    </>
  );
}
