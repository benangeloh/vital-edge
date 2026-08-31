import { NavLink } from "react-router-dom";

/** Navigasi utama.
 *
 * Rel gelap di kiri, konten putih di kanan. Ini satu-satunya tempat hijau pekat
 * dipakai secara luas — sisanya putih dan abu-abu, supaya warna tetap punya arti
 * saat dipakai untuk status. */
const NAV = [
  { to: "/", label: "Ikhtisar Armada", end: true },
  { to: "/ships", label: "Kapal" },
  { to: "/alerts", label: "Alert" },
  { to: "/telemetry", label: "Telemetry" },
  { to: "/sync", label: "Pusat Sinkronisasi" },
  { to: "/devices", label: "Perangkat" },
  { to: "/configuration", label: "Konfigurasi" },
  { to: "/reports", label: "Laporan" },
  { to: "/system", label: "Kesehatan Sistem" },
];

export function Sidebar() {
  return (
    <nav className="sidebar" aria-label="Navigasi utama">
      <div className="sidebar__brand">
        <span className="sidebar__mark" aria-hidden="true" />
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
              className={({ isActive }) => (isActive ? "navlink navlink--active" : "navlink")}
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
