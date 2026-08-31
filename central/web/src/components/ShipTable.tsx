import { Link } from "react-router-dom";
import { StatusBadge } from "./ui/StatusBadge";
import { compactNumber, relativeTime, staleness } from "@/lib/format";
import { STATUS_META, shipStatus } from "@/lib/status";
import type { FleetShip } from "@/lib/types";

/** Tabel armada.
 *
 * Padat secara sengaja: operator memindai 70 baris, bukan membaca lima kartu.
 * Kolom waktu diberi warna menurut umurnya — itu yang membuat baris bermasalah
 * menonjol tanpa perlu membaca angkanya satu per satu. */
export function ShipTable({
  ships,
  now = Date.now(),
  dense = true,
}: {
  ships: FleetShip[];
  now?: number;
  dense?: boolean;
}) {
  return (
    <div className="table-wrap">
      <table className={dense ? "table table--dense" : "table"}>
        <caption className="sr-only">
          Daftar kapal beserta status, waktu data terakhir, dan data tertunda
        </caption>
        <thead>
          <tr>
            <th scope="col">Kapal</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">Data Terakhir</th>
            <th scope="col" className="num">Sync Terakhir</th>
            <th scope="col" className="num">Data Tertunda</th>
            <th scope="col">Aliran Data</th>
            <th scope="col" className="num">Alert</th>
          </tr>
        </thead>
        <tbody>
          {ships.map((ship) => {
            const status = shipStatus(ship, now);
            const meta = STATUS_META[status];
            const dataAge = staleness(ship.last_telemetry_timestamp, now);
            const syncAge = staleness(ship.last_batch_received_at, now);
            const pending = ship.pending_estimate ?? 0;

            return (
              <tr key={ship.ship_id}>
                <th scope="row" className="table__ship">
                  <Link to={`/ships/${ship.ship_id}`} className="table__link">
                    {ship.name}
                  </Link>
                  <span className="table__slug mono">{ship.slug}</span>
                </th>
                <td>
                  <StatusBadge meta={meta} />
                </td>
                <td className={`num age age--${dataAge}`}>
                  {relativeTime(ship.last_telemetry_timestamp, now)}
                </td>
                <td className={`num age age--${syncAge}`}>
                  {relativeTime(ship.last_batch_received_at, now)}
                </td>
                <td className="num tabular">
                  {pending > 0 ? (
                    <span className={pending > 500 ? "pending pending--high" : "pending"}>
                      {compactNumber(pending)}
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                {/* Kolom ini sengaja TIDAK mengulang connection_state mentah dari
                    server. Server menurunkannya dari waktu kabar terakhir, sama
                    seperti kolom Status — hasilnya dua label yang bisa saling
                    bertentangan dalam satu baris ("Terganggu" di sebelah
                    "offline"), dan itu membuat operator berhenti untuk
                    mencocokkannya. Yang ditampilkan di sini adalah pertanyaan
                    yang berbeda: apakah aliran datanya utuh. */}
                <td className="muted">
                  {ship.has_gap ? (
                    <span className="gap-flag" title="Ada rentang sequence yang belum lengkap">
                      ada celah
                    </span>
                  ) : (
                    <span title={`Sampai sequence ${ship.last_contiguous_sequence.toLocaleString("id-ID")}`}>
                      utuh
                    </span>
                  )}
                </td>
                <td className="num muted">—</td>
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
