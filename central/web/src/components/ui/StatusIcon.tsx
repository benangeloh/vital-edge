import type { ShipStatus } from "@/lib/status";

/** Ikon garis per status. Ukurannya mengikuti pembungkusnya, jadi satu komponen
 *  dipakai di kartu besar maupun di tempat lain. */
const props = {
  viewBox: "0 0 24 24",
  fill: "none" as const,
  stroke: "currentColor",
  strokeWidth: 1.9,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export function StatusIcon({ status, className }: { status: ShipStatus; className?: string }) {
  switch (status) {
    case "online":
      return (
        <svg className={className} {...props}>
          <path d="M2.5 9.5a14 14 0 0 1 19 0" />
          <path d="M5.5 13a9.5 9.5 0 0 1 13 0" />
          <path d="M8.7 16.4a5 5 0 0 1 6.6 0" />
          <circle cx="12" cy="19.5" r="1" fill="currentColor" />
        </svg>
      );
    case "syncing":
      return (
        <svg className={className} {...props}>
          <path d="M20 11a8 8 0 0 0-14.3-4.5L4 8.5" />
          <path d="M4 4v4.5h4.5" />
          <path d="M4 13a8 8 0 0 0 14.3 4.5l1.7-2" />
          <path d="M20 20v-4.5h-4.5" />
        </svg>
      );
    case "degraded":
      return (
        <svg className={className} {...props}>
          <path d="M12 3.5 2.8 19.5h18.4L12 3.5Z" />
          <path d="M12 10v4.5" />
          <circle cx="12" cy="17" r="0.8" fill="currentColor" />
        </svg>
      );
    case "offline":
      return (
        <svg className={className} {...props}>
          <path d="M2.5 9.5a14 14 0 0 1 4-2.6" />
          <path d="M10 5.3a14 14 0 0 1 11.5 4.2" />
          <path d="M5.5 13a9.5 9.5 0 0 1 3.2-1.9" />
          <path d="M13.8 11.1a9.5 9.5 0 0 1 4.7 1.9" />
          <path d="M3 3l18 18" />
          <circle cx="12" cy="19.5" r="1" fill="currentColor" />
        </svg>
      );
    default:
      return (
        <svg className={className} {...props}>
          <path d="M12 3v8" />
          <path d="M7 6.3a8 8 0 1 0 10 0" />
        </svg>
      );
  }
}
