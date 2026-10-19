import { NavLink } from "react-router-dom";
import {
  AlertIcon,
  ChevronIcon,
  ConfigIcon,
  DeviceIcon,
  HealthIcon,
  OverviewIcon,
  ReportIcon,
  ShipIcon,
  SyncIcon,
  TelemetryIcon,
} from "@/components/shell/nav-icons";

/** Navigasi utama.
 *
 * Rel gelap di kiri, konten putih di kanan. Ini satu-satunya tempat hijau pekat
 * dipakai secara luas — sisanya putih dan abu-abu, supaya warna tetap punya arti
 * saat dipakai untuk status.
 *
 * Setiap item punya ikon supaya rail tetap bisa dibaca saat diciutkan ke mode
 * ikon-saja — label tidak dihapus dari DOM saat diciutkan, hanya terpotong oleh
 * `overflow: hidden` seiring rail menyempit, jadi transisinya terasa bergeser
 * alih-alih teks yang tiba-tiba hilang. */
const NAV = [
  { to: "/", label: "Ikhtisar Armada", end: true, Icon: OverviewIcon },
  { to: "/ships", label: "Kapal", Icon: ShipIcon },
  { to: "/alerts", label: "Alert", Icon: AlertIcon },
  { to: "/telemetry", label: "Telemetry", Icon: TelemetryIcon },
  { to: "/sync", label: "Pusat Sinkronisasi", Icon: SyncIcon },
  { to: "/devices", label: "Perangkat", Icon: DeviceIcon },
  { to: "/configuration", label: "Konfigurasi", Icon: ConfigIcon },
  { to: "/reports", label: "Laporan", Icon: ReportIcon },
  { to: "/system", label: "Kesehatan Sistem", Icon: HealthIcon },
];

export function Sidebar({
  collapsed,
  onToggleCollapsed,
}: {
  collapsed: boolean;
  onToggleCollapsed: () => void;
}) {
  return (
    <nav className={collapsed ? "sidebar sidebar--collapsed" : "sidebar"} aria-label="Navigasi utama">
      <div className="sidebar__brand">
        <img src="/spil-logo.webp" alt="" className="sidebar__mark" />
        <span className="sidebar__wordmark">
          FleetView
          <span className="sidebar__sub">SPIL</span>
        </span>
      </div>

      <ul className="sidebar__list">
        {NAV.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.end}
              title={item.label}
              className={({ isActive }) => (isActive ? "navlink navlink--active" : "navlink")}
            >
              <item.Icon className="navlink__icon" />
              <span className="navlink__label">{item.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>

      <button
        type="button"
        className="sidebar__toggle"
        onClick={onToggleCollapsed}
        aria-label={collapsed ? "Perluas navigasi" : "Ciutkan navigasi"}
        aria-expanded={!collapsed}
        title={collapsed ? "Perluas navigasi" : "Ciutkan navigasi"}
      >
        <ChevronIcon className={collapsed ? "sidebar__toggle-icon" : "sidebar__toggle-icon sidebar__toggle-icon--open"} />
      </button>
    </nav>
  );
}
