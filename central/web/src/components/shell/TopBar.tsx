import { useNavigate } from "react-router-dom";
import { auth } from "@/lib/api";
import type { FleetSummary } from "@/lib/status";

/** Bar atas: pencarian global, ringkasan status armada, alert, dan pengguna.
 *
 * Ringkasan di sini sengaja sangat ringkas — angka lengkapnya ada di halaman
 * ikhtisar. Yang perlu terlihat dari halaman mana pun hanyalah: apakah ada
 * sesuatu yang sedang bermasalah. */
export function TopBar({
  summary,
  attentionCount,
  search,
  onSearch,
  alertCount,
}: {
  summary?: FleetSummary;
  /** Berasal dari `needsAttention()` yang sama dengan panel di halaman ikhtisar.
   *  Menghitungnya ulang di sini pernah menghasilkan dua angka berbeda untuk
   *  frasa yang sama — dan itu membuat operator berhenti untuk mencocokkannya. */
  attentionCount: number;
  search: string;
  onSearch: (value: string) => void;
  alertCount: number;
}) {
  const navigate = useNavigate();

  return (
    <header className="topbar">
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
        <input
          id="global-search"
          type="search"
          className="search__input"
          placeholder="Cari kapal, ship ID, device ID…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          autoComplete="off"
        />
      </form>

      <div className="topbar__right">
        {summary && (
          <div className="fleet-pulse" aria-label="Ringkasan status armada">
            <span className="fleet-pulse__item">
              <span className="fleet-pulse__dot fleet-pulse__dot--ok" aria-hidden="true" />
              <span className="tabular">{summary.online}</span>
              <span className="fleet-pulse__label">online</span>
            </span>
            <span className="fleet-pulse__item">
              <span className="fleet-pulse__dot fleet-pulse__dot--idle" aria-hidden="true" />
              <span className="tabular">{summary.offline}</span>
              <span className="fleet-pulse__label">offline</span>
            </span>
            {attentionCount > 0 && (
              <span className="fleet-pulse__item fleet-pulse__item--warn">
                <span className="fleet-pulse__dot fleet-pulse__dot--warn" aria-hidden="true" />
                <span className="tabular">{attentionCount}</span>
                <span className="fleet-pulse__label">perlu perhatian</span>
              </span>
            )}
          </div>
        )}

        <button
          type="button"
          className="topbar__btn"
          onClick={() => navigate("/alerts")}
          aria-label={`Alert${alertCount ? `, ${alertCount} belum diakui` : ", tidak ada"}`}
        >
          Alert
          {alertCount > 0 && <span className="topbar__count tabular">{alertCount}</span>}
        </button>

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
