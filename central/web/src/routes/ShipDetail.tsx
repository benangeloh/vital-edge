import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { DeviceHealthPanel } from "@/components/health/DeviceHealthPanel";
import { DateRangeBar } from "@/components/logger/DateRangeBar";
import { LoggerPanel } from "@/components/logger/LoggerPanel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState, ErrorState, Field, Loading, Panel, Section } from "@/components/ui/Primitives";
import { absoluteTime, compactNumber, relativeTime, shortId } from "@/lib/format";
import { PRESETS, presetRange, type LoggerRange } from "@/lib/logger";
import { useShip, useShipSensors, useSyncStatus, useTelemetry } from "@/lib/queries";
import { STATUS_META, shipStatus } from "@/lib/status";

const TABS = ["Logger", "Grafik", "Status kapal"] as const;
type Tab = (typeof TABS)[number];

/** Warna per besaran, konsisten di seluruh aplikasi, sehingga bentuk kurva bisa
 *  dikenali tanpa membaca judulnya. */
const METRIC_COLORS: Record<string, string> = {
  rpm: "var(--green-primary)",
  fuel_level: "var(--status-sync)",
  pressure: "var(--status-warn)",
  temperature: "var(--red-brand)",
};

function initialRange(): LoggerRange {
  const preset = PRESETS.find((p) => p.id === "24h")!;
  return { presetId: preset.id, ...presetRange(preset) };
}

export function ShipDetail() {
  const { shipId } = useParams<{ shipId: string }>();
  const [tab, setTab] = useState<Tab>("Logger");
  const [range, setRange] = useState<LoggerRange>(initialRange);

  const { data: ship, isLoading, error } = useShip(shipId);
  const { data: sync } = useSyncStatus(shipId);
  const { data: sensors } = useShipSensors(shipId);
  const { data: telemetry } = useTelemetry(tab === "Grafik" ? shipId : undefined, range);

  if (isLoading) return <Loading label="Memuat kapal" />;
  if (error) return <ErrorState error={error} />;
  if (!ship || !shipId) return <EmptyState title="Kapal tidak ditemukan" />;

  const charts = (telemetry?.series ?? []).filter((s) => s.field === "value");

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
               menampilkan "Online" untuk kapal yang di tabel tertulis offline:
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
        <p className="ship-meta">
          <span className="mono">{ship.slug}</span>
          <span>ID {shortId(ship.ship_id, 13)}</span>
          {ship.imo_number && <span>IMO {ship.imo_number}</span>}
          {sync?.last_batch_received_at && (
            <span>Data terakhir {relativeTime(sync.last_batch_received_at)} lalu</span>
          )}
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
        {tab === "Logger" && (
          <Section
            title="Logger data kapal"
            description="Pembacaan sensor yang dikirim kapal, terbaru di atas. Pilih rentang tanggal untuk melihat data lama."
          >
            <LoggerPanel shipId={shipId} shipSlug={ship.slug} />
          </Section>
        )}

        {tab === "Grafik" && (
          <Section title="Grafik sensor" description="Mengikuti rentang tanggal yang dipilih.">
            <div className="panel logger__controls">
              <DateRangeBar value={range} onChange={setRange} />
            </div>
            {charts.length === 0 ? (
              <EmptyState
                title="Belum ada data pada rentang ini"
                hint="Perluas rentang tanggal, atau cek tab Logger untuk memastikan kapal mengirim data."
              />
            ) : (
              <div className="charts">
                {charts.map((series) => {
                  const last = series.points.at(-1)?.[1];
                  return (
                    <Panel key={`${series.sensor_id}-${series.measurement}`}>
                      <div className="chart__head">
                        <h3 className="chart__title">
                          {series.sensor_id}
                          <span className="chart__metric"> {series.measurement}</span>
                        </h3>
                        <span className="chart__value tabular">
                          {typeof last === "number" ? last.toFixed(1) : "Tidak ada"}
                          <span className="chart__unit">{series.unit ?? ""}</span>
                        </span>
                      </div>
                      <TimeSeriesChart
                        series={series}
                        color={METRIC_COLORS[series.measurement] ?? "var(--green-primary)"}
                        label={series.sensor_id}
                      />
                    </Panel>
                  );
                })}
              </div>
            )}
            {telemetry?.bucket_used && telemetry.bucket_used !== "raw" && (
              <p className="note">
                Ditampilkan sebagai rata-rata per {telemetry.window}, bukan data mentah. Untuk data
                mentah, persempit rentang atau buka tab Logger.
              </p>
            )}
          </Section>
        )}

        {tab === "Status kapal" && (
          <>
            <Section title="Identitas">
              <Panel>
                <dl className="fields">
                  <Field label="Nama">{ship.name}</Field>
                  <Field label="Kode kapal">
                    <span className="mono">{ship.slug}</span>
                  </Field>
                  <Field label="IMO">{ship.imo_number ?? "Belum diisi"}</Field>
                  <Field label="Call sign">{ship.call_sign ?? "Belum diisi"}</Field>
                  <Field label="Versi konfigurasi">
                    <span className="mono">{ship.active_config_version ?? "Tidak ada"}</span>
                  </Field>
                  <Field label="Sensor terdaftar">{sensors?.length ?? 0}</Field>
                </dl>
              </Panel>
            </Section>

            <Section
              title="Sinkronisasi data"
              description="Seberapa lengkap data dari kapal sudah sampai di pusat."
            >
              <Panel>
                {sync ? (
                  <dl className="fields">
                    <Field label="Urutan data lengkap sampai">
                      <span className="tabular">
                        {sync.last_contiguous_sequence.toLocaleString("id-ID")}
                      </span>
                    </Field>
                    <Field label="Urutan tertinggi diterima">
                      <span className="tabular">
                        {sync.highest_sequence_seen.toLocaleString("id-ID")}
                      </span>
                    </Field>
                    <Field label="Rentang belum lengkap">
                      {sync.has_gap ? (
                        <span className="gap-flag">Ada, sebagian data belum sampai</span>
                      ) : (
                        "Tidak ada"
                      )}
                    </Field>
                    <Field label="Total batch">
                      <span className="tabular">{compactNumber(sync.total_batches)}</span>
                    </Field>
                    <Field label="Total pembacaan">
                      <span className="tabular">{compactNumber(sync.total_records)}</span>
                    </Field>
                    <Field label="Menunggu dikirim dari kapal">
                      <span className="tabular">{compactNumber(sync.pending_estimate)}</span>
                    </Field>
                  </dl>
                ) : (
                  <EmptyState title="Belum ada data sinkronisasi" />
                )}
              </Panel>
            </Section>

            <Section
              title="Kondisi perangkat"
              description="Dilaporkan kapal lewat heartbeat, bukan dibaca dari pusat."
            >
              <DeviceHealthPanel
                health={sync?.edge_health ?? null}
                lastHeartbeatAt={sync?.last_heartbeat_at ?? null}
              />
            </Section>

            <Section title="Perangkat di kapal">
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
                        <td className="muted">{d.hardware ?? "Tidak ada"}</td>
                        <td className="muted">{d.field_device ?? "Tidak ada"}</td>
                        <td className="mono">{d.agent_version ?? "Tidak ada"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {ship.devices.length === 0 && <EmptyState title="Belum ada perangkat terdaftar" />}
              </Panel>
            </Section>

            <Section
              title="Sensor terdaftar"
              description="Sensor yang belum dikenal didaftarkan otomatis saat pertama terlihat."
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
                        <td className="muted">{s.unit ?? "Tidak ada"}</td>
                        <td className="muted">{s.status}</td>
                        <td className="num muted" title={absoluteTime(s.last_seen_at)}>
                          {s.last_seen_at ? `${relativeTime(s.last_seen_at)} lalu` : "Belum ada"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {(sensors ?? []).length === 0 && <EmptyState title="Belum ada sensor terlihat" />}
              </Panel>
            </Section>
          </>
        )}
      </div>
    </>
  );
}
