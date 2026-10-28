import { NavLink, useNavigate } from "react-router-dom";
import { auth } from "@/lib/api";
import { SearchIcon } from "@/components/shell/nav-icons";
import type { FleetSummary } from "@/lib/status";

/** Dua modul saja, jadi navigasinya berupa dua tautan di bilah atas, bukan rel
 *  samping yang memakan seperlima lebar layar. */
const NAV = [
  { to: "/", label: "Ringkasan", end: true },
  { to: "/ships", label: "Kapal", end: false },
];

/** Bilah atas: merek, navigasi, pencarian, denyut armada, dan keluar.
 *
 * Denyut armada sengaja sangat ringkas — angka lengkapnya ada di halaman
 * ringkasan. Yang perlu terlihat dari halaman mana pun hanyalah: apakah ada
 * sesuatu yang sedang bermasalah. */
export function TopBar({
  summary,
  attentionCount,
  search,
  onSearch,
}: {
  summary?: FleetSummary;
  /** Berasal dari `needsAttention()` yang sama dengan panel di halaman ringkasan.
   *  Menghitungnya ulang di sini pernah menghasilkan dua angka berbeda untuk
   *  frasa yang sama — dan itu membuat operator berhenti untuk mencocokkannya. */
  attentionCount: number;
  search: string;
  onSearch: (value: string) => void;
}) {
  const navigate = useNavigate();

  return (
    <header className="topbar">
      <div className="topbar__inner">
        <NavLink to="/" className="brand" aria-label="FleetView SPIL, ke ringkasan armada">
          <img src="/spil-logo.webp" alt="" className="brand__mark" />
          <span className="brand__name">
            FleetView<span className="brand__sub">SPIL</span>
          </span>
        </NavLink>

        <nav className="mainnav" aria-label="Navigasi utama">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className="mainnav__link"
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <form
          className="search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            navigate("/ships");
          }}
        >
          <label htmlFor="global-search" className="sr-only">
            Cari kapal berdasarkan nama, ship ID, atau device ID
          </label>
          <SearchIcon className="search__icon" />
          <input
            id="global-search"
            type="search"
            className="search__input"
            placeholder="Cari kapal"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            autoComplete="off"
          />
        </form>

        {summary && (
          <div className="fleet-pulse" aria-label="Ringkasan status armada">
            <span className="fleet-pulse__item">
              <span className="fleet-pulse__dot fleet-pulse__dot--ok" aria-hidden="true" />
              <span className="tabular">{summary.online}</span> online
            </span>
            <span className="fleet-pulse__item">
              <span className="fleet-pulse__dot fleet-pulse__dot--idle" aria-hidden="true" />
              <span className="tabular">{summary.offline}</span> offline
            </span>
            {attentionCount > 0 && (
              <span className="fleet-pulse__item fleet-pulse__item--warn">
                <span className="fleet-pulse__dot fleet-pulse__dot--warn" aria-hidden="true" />
                <span className="tabular">{attentionCount}</span> perlu perhatian
              </span>
            )}
          </div>
        )}

        <button
          type="button"
          className="topbar__btn"
          onClick={() => {
            auth.clear();
            window.location.reload();
          }}
        >
          Keluar
        </button>
      </div>
    </header>
  );
}
