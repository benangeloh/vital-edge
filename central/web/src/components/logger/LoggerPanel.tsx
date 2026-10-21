import { useState } from "react";
import { DateRangeBar } from "./DateRangeBar";
import { SensorPicker } from "./SensorPicker";
import { EmptyState, ErrorState, Loading } from "@/components/ui/Primitives";
import {
  EXPORT_MAX_ROWS,
  PRESETS,
  csvFileName,
  describeRange,
  presetRange,
  readingsToCsv,
  type LoggerRange,
} from "@/lib/logger";
import { fetchReadingsForExport, useReadings, useShipSensors } from "@/lib/queries";

const PAGE_SIZE = 50;

const QUALITY_LABEL: Record<string, string> = {
  good: "Baik",
  stale: "Basi",
  suspect: "Meragukan",
  substituted: "Pengganti",
};

function formatValue(value: number | string): string {
  return typeof value === "number"
    ? value.toLocaleString("id-ID", { maximumFractionDigits: 3, useGrouping: false })
    : value;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("id-ID", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function initialRange(): LoggerRange {
  const preset = PRESETS.find((p) => p.id === "24h")!;
  return { presetId: preset.id, ...presetRange(preset) };
}

/** Logger satu kapal: pembacaan mentah dalam rentang tanggal pilihan, dengan
 *  saringan sensor dan unduhan CSV. Inilah yang dibaca teknisi untuk memastikan
 *  data dari kapal benar-benar masuk dan wajar. */
export function LoggerPanel({ shipId, shipSlug }: { shipId: string; shipSlug: string }) {
  const [range, setRange] = useState<LoggerRange>(initialRange);
  const [sensorId, setSensorId] = useState("");
  const [page, setPage] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const { data: sensors } = useShipSensors(shipId);
  const readings = useReadings(shipId, {
    range,
    sensorId: sensorId || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  const data = readings.data;
  const rows = data?.rows ?? [];

  function changeRange(next: LoggerRange) {
    setRange(next);
    setPage(0);
  }

  async function download() {
    setExporting(true);
    setExportNote(null);
    setExportError(null);
    try {
      const { rows: all, truncated } = await fetchReadingsForExport(
        shipId,
        range,
        sensorId || undefined,
      );
      const blob = new Blob([readingsToCsv(all)], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = csvFileName(shipSlug, range.start, range.stop);
      link.click();
      URL.revokeObjectURL(url);
      setExportNote(
        truncated
          ? `Hanya ${all.length.toLocaleString("id-ID")} baris terbaru yang diunduh. Persempit rentang untuk mendapat sisanya.`
          : `${all.length.toLocaleString("id-ID")} baris diunduh.`,
      );
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Unduhan gagal.");
    } finally {
      setExporting(false);
    }
  }

  const shownRange =
    data && describeRange(new Date(data.start), new Date(data.stop));

  return (
    <div className="logger">
      <div className="panel logger__controls">
        <DateRangeBar value={range} onChange={changeRange} />
        <div className="logger__sensor">
          <SensorPicker
            sensors={sensors ?? []}
            value={sensorId}
            onChange={(next) => {
              setSensorId(next);
              setPage(0);
            }}
          />
        </div>
      </div>

      <div className="logger__bar">
        <p className="logger__summary">
          {data
            ? `${
                page === 0
                  ? `Menampilkan ${rows.length} pembacaan terbaru`
                  : `Menampilkan pembacaan ke-${page * PAGE_SIZE + 1} sampai ${page * PAGE_SIZE + rows.length}`
              } dari ${shownRange}`
            : "Memuat pembacaan"}
          {range.presetId ? ". Diperbarui otomatis." : "."}
        </p>
        <div className="logger__export">
          <button
            type="button"
            className="btn btn--outline"
            onClick={download}
            disabled={exporting}
          >
            {exporting ? "Menyiapkan unduhan" : "Unduh CSV"}
          </button>
          <span className="logger__hint">
            maksimal {EXPORT_MAX_ROWS.toLocaleString("id-ID")} baris per unduhan
          </span>
        </div>
      </div>
      {exportNote && (
        <p className="logger__note" role="status">
          {exportNote}
        </p>
      )}
      {exportError && (
        <p className="logger__note logger__note--error" role="alert">
          {exportError}
        </p>
      )}

      {readings.isLoading && !data ? (
        <Loading label="Memuat pembacaan" />
      ) : readings.error ? (
        <ErrorState error={readings.error} onRetry={() => readings.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="Belum ada pembacaan pada rentang ini"
          hint="Perluas rentang tanggal atau pilih semua sensor. Kapal yang baru dipasang butuh beberapa menit sampai datanya sampai."
        />
      ) : (
        <div className="table-wrap">
          <table className="table table--dense logger__table">
            <caption className="sr-only">Pembacaan sensor kapal, terbaru di atas</caption>
            <thead>
              <tr>
                <th scope="col">Waktu</th>
                <th scope="col">Sensor</th>
                <th scope="col">Metrik</th>
                <th scope="col" className="num">Nilai</th>
                <th scope="col">Kualitas</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.time}-${r.sensor_id}-${r.field}-${i}`}>
                  <td className="tabular">{formatTime(r.time)}</td>
                  <td className="mono">{r.sensor_id}</td>
                  <td>
                    {r.metric}
                    {r.field !== "value" && <span className="muted"> ({r.field})</span>}
                  </td>
                  <td className="num tabular">
                    {formatValue(r.value)}
                    {r.unit && <span className="logger__unit"> {r.unit}</span>}
                  </td>
                  <td>
                    <span className={`quality quality--${r.quality ?? "none"}`}>
                      {r.quality ? (QUALITY_LABEL[r.quality] ?? r.quality) : "Tidak ada"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(page > 0 || data?.has_more) && (
        <nav className="pager" aria-label="Halaman pembacaan">
          <p className="pager__info tabular">Halaman {page + 1}</p>
          <div className="pager__btns">
            <button
              type="button"
              className="btn btn--outline"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
            >
              Sebelumnya
            </button>
            <button
              type="button"
              className="btn btn--outline"
              disabled={!data?.has_more}
              onClick={() => setPage(page + 1)}
            >
              Berikutnya
            </button>
          </div>
        </nav>
      )}
    </div>
  );
}
