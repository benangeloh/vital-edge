import { useEffect, useId, useMemo, useRef, useState } from "react";
import { staleness } from "@/lib/format";
import { metricLabel } from "@/lib/metrics";
import { agoPhrase } from "@/lib/narrative";

export interface SensorOption {
  sensor_id: string;
  metric: string;
  unit: string | null;
  last_seen_at: string | null;
}

type Item =
  | { kind: "all" }
  | { kind: "sensor"; sensor: SensorOption; group: string };

/** Pemilih sensor: bisa dicari, dikelompokkan menurut jenis ukuran, dan
 *  menunjukkan sensor mana yang sedang mengirim data.
 *
 *  Pilihan bawaan browser menampilkan puluhan id mentah dalam satu daftar datar.
 *  Dengan kapal yang punya belasan sensor, teknisi harus menebak id mana yang
 *  dimaksud; di sini ia cukup mengetik "suhu" atau melihat kelompoknya. */
export function SensorPicker({
  sensors,
  value,
  onChange,
}: {
  sensors: SensorOption[];
  value: string;
  onChange: (sensorId: string) => void;
}) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const items = useMemo<Item[]>(() => {
    const needle = query.trim().toLowerCase();
    const matched = sensors
      .map((sensor) => ({ sensor, group: metricLabel(sensor.metric) }))
      .filter(
        ({ sensor, group }) =>
          !needle ||
          [sensor.sensor_id, group, sensor.metric, sensor.unit ?? ""].some((text) =>
            text.toLowerCase().includes(needle),
          ),
      )
      .sort(
        (a, b) =>
          a.group.localeCompare(b.group, "id") || a.sensor.sensor_id.localeCompare(b.sensor.sensor_id),
      )
      .map((entry) => ({ kind: "sensor" as const, ...entry }));
    return needle ? matched : [{ kind: "all" }, ...matched];
  }, [sensors, query]);

  function openList() {
    setQuery("");
    const index = value ? items.findIndex((i) => i.kind === "sensor" && i.sensor.sensor_id === value) : 0;
    setActive(Math.max(0, index));
    setOpen(true);
  }

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }

  function choose(item: Item) {
    onChange(item.kind === "all" ? "" : item.sensor.sensor_id);
    close();
  }

  useEffect(() => {
    if (open) search.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Pencarian berubah, daftar berubah; kursor kembali ke baris pertama.
  useEffect(() => setActive(0), [query]);

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => Math.min(items.length - 1, i + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = items[active];
      if (item) choose(item);
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }

  const current = sensors.find((s) => s.sensor_id === value);
  const optionId = (index: number) => `${id}-opt-${index}`;

  let previousGroup = "";

  return (
    <div className="picker" ref={root}>
      <label className="picker__label" htmlFor={`${id}-trigger`}>
        Sensor
      </label>
      <button
        ref={trigger}
        id={`${id}-trigger`}
        type="button"
        role="combobox"
        className="picker__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            openList();
          }
        }}
      >
        {value ? (
          <span className="picker__value">
            <span className="picker__id">{value}</span>
            {current && <span className="picker__meta">{metricLabel(current.metric)}</span>}
          </span>
        ) : (
          <span className="picker__value">Semua sensor</span>
        )}
        <svg className="picker__chevron" viewBox="0 0 20 20" aria-hidden="true">
          <path d="m5 7.5 5 5 5-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="picker__panel" onKeyDown={onKeyDown}>
          <input
            ref={search}
            type="search"
            role="searchbox"
            className="picker__search"
            placeholder="Cari sensor, jenis, atau satuan"
            aria-label="Cari sensor"
            aria-controls={`${id}-list`}
            aria-activedescendant={items.length > 0 ? optionId(active) : undefined}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoComplete="off"
          />
          <ul id={`${id}-list`} role="listbox" aria-label="Sensor" className="picker__list">
            {items.length === 0 && (
              <li className="picker__empty" role="presentation">
                Tidak ada sensor yang cocok dengan "{query}".
              </li>
            )}
            {items.map((item, index) => {
              if (item.kind === "all") {
                return (
                  <li
                    key="all"
                    id={optionId(index)}
                    role="option"
                    aria-selected={value === ""}
                    className={`picker__opt${index === active ? " picker__opt--active" : ""}`}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => choose(item)}
                  >
                    <span className="picker__id">Semua sensor</span>
                    <span className="picker__meta">{sensors.length} sensor terdaftar</span>
                  </li>
                );
              }
              const { sensor, group } = item;
              const heading = group !== previousGroup;
              previousGroup = group;
              const seen = staleness(sensor.last_seen_at);
              const status =
                seen === "none"
                  ? "Belum ada data"
                  : seen === "fresh"
                    ? "Aktif"
                    : `Terakhir ${agoPhrase(sensor.last_seen_at) ?? ""}`.trim();
              return (
                <li key={sensor.sensor_id} role="presentation">
                  {heading && (
                    <span className="picker__group" role="presentation">
                      {group}
                    </span>
                  )}
                  <span
                    id={optionId(index)}
                    role="option"
                    aria-selected={value === sensor.sensor_id}
                    className={`picker__opt${index === active ? " picker__opt--active" : ""}`}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => choose(item)}
                  >
                    <span className="picker__id">{sensor.sensor_id}</span>
                    <span className="picker__meta">
                      {sensor.unit ? `Satuan ${sensor.unit}` : "Tanpa satuan"}
                    </span>
                    <span className={`picker__state picker__state--${seen}`}>
                      <span className="picker__dot" aria-hidden="true" />
                      {status}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
