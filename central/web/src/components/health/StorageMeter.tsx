export type MeterLevel = "ok" | "warn" | "crit";

const LEVEL_TEXT: Record<MeterLevel, string> = {
  ok: "Normal",
  warn: "Perlu perhatian",
  crit: "Kritis",
};

/** Ambang bawaan, sama dengan `disk_warn_percent` dan `disk_critical_percent`
 *  di server dan edge. Untuk disk host, pakai level dari server supaya ambang
 *  yang diubah lewat konfigurasi tidak menyimpang dari yang ditampilkan. */
export function levelForPercent(percent: number, warn = 80, critical = 90): MeterLevel {
  if (percent >= critical) return "crit";
  if (percent >= warn) return "warn";
  return "ok";
}

export function StorageMeter({
  label,
  percent,
  detail,
  level,
}: {
  label: string;
  percent: number;
  detail?: string;
  level?: MeterLevel;
}) {
  const clamped = Math.min(100, Math.max(0, percent));
  const tone = level ?? levelForPercent(clamped);
  const shown = `${clamped.toFixed(1).replace(".", ",")}%`;

  return (
    <div className="meter">
      <div className="meter__head">
        <span className="meter__label">{label}</span>
        <span className="meter__value mono">{shown}</span>
      </div>
      <div
        className="meter__track"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={clamped}
        aria-valuetext={`${shown}, ${LEVEL_TEXT[tone]}`}
      >
        <div className={`meter__fill meter__fill--${tone}`} style={{ width: `${clamped}%` }} />
      </div>
      <div className="meter__foot">
        <span className={`meter__level meter__level--${tone}`}>{LEVEL_TEXT[tone]}</span>
        {detail && <span className="meter__detail">{detail}</span>}
      </div>
    </div>
  );
}
