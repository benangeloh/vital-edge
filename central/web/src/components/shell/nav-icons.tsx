interface IconProps {
  className?: string;
}

/** Ikon garis 24x24, minimal — dipilih supaya rail navigasi tetap terbaca saat
 * diciutkan ke mode ikon-saja. Digambar tangan, bukan dari paket ikon, supaya
 * tidak menambah dependency untuk sembilan bentuk sederhana. */
const svgProps = {
  viewBox: "0 0 24 24",
  fill: "none" as const,
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export function OverviewIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" />
    </svg>
  );
}

export function ShipIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M4 15h16l-2.2 4.3a1 1 0 0 1-.9.7H7.1a1 1 0 0 1-.9-.7L4 15Z" />
      <path d="M12 15V4" />
      <path d="M12 4.5 17 8h-5" />
    </svg>
  );
}

export function AlertIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M12 3.5 21.5 20h-19L12 3.5Z" />
      <path d="M12 10v4" />
      <circle cx="12" cy="17" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function TelemetryIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M3 12h3.5l2-6.5 3 13 2.5-9.5 1.5 3h5.5" />
    </svg>
  );
}

export function SyncIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M4.5 12a7.5 7.5 0 0 1 13-5" />
      <path d="M19.5 12a7.5 7.5 0 0 1-13 5" />
      <path d="M17 3.5v4h-4" />
      <path d="M7 20.5v-4h4" />
    </svg>
  );
}

export function DeviceIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <rect x="5" y="3.5" width="14" height="17" rx="2" />
      <path d="M9 8h6M9 12h6M9 16h3" />
    </svg>
  );
}

export function ConfigIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M4 6h8" />
      <path d="M16.5 6H20" />
      <circle cx="13.5" cy="6" r="2" />
      <path d="M4 12h2.5" />
      <path d="M11 12H20" />
      <circle cx="8.5" cy="12" r="2" />
      <path d="M4 18h8" />
      <path d="M16.5 18H20" />
      <circle cx="13.5" cy="18" r="2" />
    </svg>
  );
}

export function ReportIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M7 3h6.5L18 7.5V20a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M13.5 3v4.5H18" />
      <path d="M9 12.5h6M9 16h6" />
    </svg>
  );
}

export function HealthIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M12 20.5s-7.2-4.4-9.4-9C1 8 3.2 4.2 6.8 4.2c2 0 3.5 1.1 5.2 3 1.7-1.9 3.2-3 5.2-3 3.6 0 5.8 3.8 4.2 7.3-2.2 4.6-9.4 9-9.4 9Z" />
      <path d="M6 12h2.5l1.5-3 2 6 1.5-3H16" />
    </svg>
  );
}

export function ChevronIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svgProps}>
      <path d="M9 5.5 15 12l-6 6.5" />
    </svg>
  );
}
