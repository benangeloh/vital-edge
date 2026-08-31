import type { StatusMeta } from "@/lib/status";

/** Lencana status: warna DAN simbol, tidak pernah warna saja.
 *
 * Dashboard ini dipakai di layar anjungan yang kadang terkena matahari langsung,
 * dan sebagian operator buta warna. Simbolnya bukan hiasan — itu yang membuat
 * status tetap terbaca ketika warnanya tidak. */
export function StatusBadge({ meta, compact = false }: { meta: StatusMeta; compact?: boolean }) {
  return (
    <span className={`badge badge--${meta.tone}`}>
      <span className="badge__glyph" aria-hidden="true">
        {meta.glyph}
      </span>
      {compact ? <span className="sr-only">{meta.label}</span> : meta.label}
    </span>
  );
}
