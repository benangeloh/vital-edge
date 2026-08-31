import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { TelemetrySeries } from "@/lib/types";

/** Chart deret waktu memakai uPlot.
 *
 * uPlot dipilih (keputusan D9 di dokumen arsitektur) karena ukurannya ~45 KB dan
 * sanggup merender ratusan ribu titik dengan mulus. Library chart yang lebih
 * umum mulai tersendat pada kepadatan telemetry, dan tampilannya juga membawa
 * gaya "dashboard generik" yang justru ingin dihindari.
 *
 * Gaya visualnya sengaja tenang: tanpa gridline vertikal, tanpa legenda kotak,
 * satu warna per chart. Yang dibaca operator adalah bentuk kurvanya, bukan
 * hiasannya. */
export function TimeSeriesChart({
  series,
  height = 160,
  color = "var(--green-primary)",
  label,
}: {
  series: TelemetrySeries | undefined;
  height?: number;
  color?: string;
  label: string;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);

  useEffect(() => {
    const element = holder.current;
    if (!element) return;

    const points = (series?.points ?? []).filter(
      (p): p is [string, number] => typeof p[1] === "number",
    );
    const xs = points.map((p) => new Date(p[0]).getTime() / 1000);
    const ys = points.map((p) => p[1]);

    // Warna diambil dari token CSS, bukan di-hardcode: uPlot menggambar ke
    // canvas dan tidak bisa memakai var() secara langsung.
    const resolved = color.startsWith("var(")
      ? getComputedStyle(document.documentElement)
          .getPropertyValue(color.slice(4, -1))
          .trim() || "#34854c"
      : color;

    const options: uPlot.Options = {
      width: element.clientWidth || 600,
      height,
      padding: [12, 8, 0, 0],
      legend: { show: false },
      cursor: { y: false, drag: { x: true, y: false } },
      scales: { x: { time: true } },
      axes: [
        {
          stroke: "#5c6b64",
          grid: { show: false },
          ticks: { show: false },
          font: "11px Inter, system-ui, sans-serif",
        },
        {
          stroke: "#5c6b64",
          grid: { stroke: "#e4e8e5", width: 1 },
          ticks: { show: false },
          font: "11px Inter, system-ui, sans-serif",
          size: 44,
        },
      ],
      series: [
        {},
        {
          label,
          stroke: resolved,
          width: 1.75,
          fill: `${resolved}14`,
          points: { show: points.length < 40 },
        },
      ],
    };

    chart.current?.destroy();
    chart.current = new uPlot(options, [xs, ys], element);

    const observer = new ResizeObserver(([entry]) => {
      if (entry) chart.current?.setSize({ width: entry.contentRect.width, height });
    });
    observer.observe(element);

    return () => {
      observer.disconnect();
      chart.current?.destroy();
      chart.current = null;
    };
  }, [series, height, color, label]);

  const empty = !series || series.points.length === 0;

  return (
    <div className="chart">
      <div ref={holder} className="chart__canvas" aria-hidden="true" />
      {empty && <p className="chart__empty">Belum ada data pada rentang ini</p>}
      {/* Chart canvas tidak bisa dibaca pembaca layar. Ringkasan teks ini yang
          membuat datanya tetap terjangkau. */}
      <p className="sr-only">
        {empty
          ? `${label}: tidak ada data`
          : `${label}: ${series.points.length} titik data, nilai terakhir ${
              series.points[series.points.length - 1]?.[1]
            } ${series.unit ?? ""}`}
      </p>
    </div>
  );
}
