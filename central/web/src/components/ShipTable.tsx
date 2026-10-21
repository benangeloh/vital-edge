import { Link } from "react-router-dom";
import { StatusBadge } from "./ui/StatusBadge";
import { absoluteTime, compactNumber, relativeTime, staleness } from "@/lib/format";
import { conditionPhrase } from "@/lib/narrative";
import { STATUS_META, shipStatus } from "@/lib/status";
import type { FleetShip } from "@/lib/types";

/** Tabel armada.
 *
 * Satu baris menjawab empat pertanyaan operator: bagaimana statusnya, apa
 * artinya dalam kalimat, kapan data terakhir masuk, dan ke mana klik untuk
 * melihat lebih jauh. Kolom teknis seperti sequence dan watermark ada di
 * halaman detail kapal, bukan di sini. */
export function ShipTable({
  ships,
  now = Date.now(),
  dense = false,
}: {
  ships: FleetShip[];
  now?: number;
  dense?: boolean;
}) {
  return (
    <div className="table-wrap">
      <table className={dense ? "table table--dense" : "table"}>
        <caption className="sr-only">
          Daftar kapal beserta status, kondisi, waktu data terakhir, dan data tertunda
        </caption>
        <thead>
          <tr>
            <th scope="col">Status</th>
            <th scope="col">Kapal</th>
            <th scope="col">Kondisi</th>
            <th scope="col" className="num">Update Terakhir</th>
            <th scope="col" className="num">Data Tertunda</th>
            <th scope="col" className="table__action">Aksi</th>
          </tr>
        </thead>
        <tbody>
          {ships.map((ship) => {
            const status = shipStatus(ship, now);
            const meta = STATUS_META[status];
            const lastAt = ship.last_telemetry_timestamp ?? ship.last_batch_received_at;
            const age = staleness(lastAt, now);
            const pending = ship.pending_estimate ?? 0;

            return (
              <tr key={ship.ship_id}>
                <td>
                  <StatusBadge meta={meta} />
                </td>
                <th scope="row" className="table__ship">
                  <Link to={`/ships/${ship.ship_id}`} className="table__link">
                    {ship.name}
                  </Link>
                  <span className="table__slug mono">{ship.slug}</span>
                </th>
                <td className="table__condition">{conditionPhrase(ship, now)}</td>
                <td className="num">
                  {lastAt ? (
                    <>
                      <span className="table__when">{absoluteTime(lastAt)}</span>
                      <span className={`table__ago age--${age}`}>
                        {relativeTime(lastAt, now)} lalu
                      </span>
                    </>
                  ) : (
                    <span className="muted">Belum ada</span>
                  )}
                </td>
                <td className="num tabular">
                  {pending > 0 ? (
                    <span className={pending > 500 ? "pending pending--high" : "pending"}>
                      {compactNumber(pending)}
                    </span>
                  ) : (
                    <span className="muted">Tidak ada</span>
                  )}
                </td>
                <td className="table__action">
                  <Link
                    to={`/ships/${ship.ship_id}`}
                    className="btn btn--outline btn--small"
                    aria-label={`Lihat detail ${ship.name}`}
                  >
                    Lihat detail
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {ships.length === 0 && (
        <p className="table__empty">Tidak ada kapal yang cocok dengan filter.</p>
      )}
    </div>
  );
}
