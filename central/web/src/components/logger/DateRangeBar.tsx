import { useEffect, useId, useState } from "react";
import {
  PRESETS,
  fromInputValue,
  presetRange,
  rangeError,
  toInputValue,
  type LoggerRange,
} from "@/lib/logger";

export type { LoggerRange };

/** Pilihan rentang waktu: tombol cepat untuk kebutuhan sehari-hari, dan dua
 *  isian tanggal untuk rentang tertentu, misalnya satu pelayaran. */
export function DateRangeBar({
  value,
  onChange,
}: {
  value: LoggerRange;
  onChange: (range: LoggerRange) => void;
}) {
  const id = useId();
  const [from, setFrom] = useState(toInputValue(value.start));
  const [to, setTo] = useState(toInputValue(value.stop));
  const [error, setError] = useState<string | null>(null);

  // Memilih tombol cepat mengubah isian supaya selalu cocok dengan yang tampil.
  useEffect(() => {
    setFrom(toInputValue(value.start));
    setTo(toInputValue(value.stop));
    setError(null);
  }, [value.start, value.stop]);

  function apply() {
    const start = fromInputValue(from);
    const stop = fromInputValue(to);
    const problem = rangeError(start, stop);
    setError(problem);
    if (!problem && start && stop) onChange({ presetId: null, start, stop });
  }

  return (
    <div className="range">
      <div className="range__presets" role="group" aria-label="Rentang cepat">
        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            className={value.presetId === preset.id ? "chip chip--on" : "chip"}
            aria-pressed={value.presetId === preset.id}
            onClick={() => onChange({ presetId: preset.id, ...presetRange(preset) })}
          >
            {preset.label}
          </button>
        ))}
      </div>

      <div className="range__custom">
        <div className="range__field">
          <label htmlFor={`${id}-from`}>Dari</label>
          <input
            id={`${id}-from`}
            type="datetime-local"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${id}-error` : undefined}
          />
        </div>
        <div className="range__field">
          <label htmlFor={`${id}-to`}>Sampai</label>
          <input
            id={`${id}-to`}
            type="datetime-local"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${id}-error` : undefined}
          />
        </div>
        <button type="button" className="btn btn--primary" onClick={apply}>
          Tampilkan
        </button>
      </div>

      {error && (
        <p id={`${id}-error`} className="range__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
