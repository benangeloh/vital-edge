import { useEffect, useRef, useState } from "react";
import { FormError } from "./ui/FormError";
import { Modal } from "./ui/Modal";
import { useDecommissionShip } from "@/lib/queries";
import type { FleetShip } from "@/lib/types";

/** Menonaktifkan kapal, bukan menghapusnya.
 *
 *  Barisnya sengaja dipertahankan: ledger batch dan telemetry merujuk padanya,
 *  dan datanya sering dibutuhkan setelah kapal tidak beroperasi (audit, klaim
 *  asuransi). Yang dicabut adalah aksesnya. Kode kapal harus diketik karena daftar
 *  berisi banyak baris yang mirip dan tombolnya mudah salah tekan. */
export function DecommissionShipDialog({
  ship,
  onClose,
}: {
  ship: FleetShip;
  onClose: () => void;
}) {
  const decommission = useDecommissionShip(ship.ship_id);
  const [typed, setTyped] = useState("");
  const fieldRef = useRef<HTMLInputElement>(null);
  const confirmed = typed.trim().toUpperCase() === ship.slug.toUpperCase();

  useEffect(() => {
    fieldRef.current?.focus();
  }, []);

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!confirmed) return;
    decommission.mutate(undefined, { onSuccess: onClose });
  };

  return (
    <Modal titleId="decommission-title" onClose={onClose}>
      <form onSubmit={submit}>
        <h2 className="modal__title" id="decommission-title">
          Nonaktifkan {ship.name}?
        </h2>
        <p className="modal__desc">Gunakan ini untuk kapal yang dijual, disewakan, atau pensiun.</p>

        <ul className="modal__consequences">
          <li>Semua akses kapal dicabut. Komputer kapal tidak bisa lagi mengirim data.</li>
          <li>Kapal hilang dari daftar dan ringkasan armada.</li>
          <li>Seluruh data dan riwayatnya tetap tersimpan.</li>
        </ul>
        <p className="modal__warn">
          Pastikan data di komputer kapal sudah terkirim semua. Setelah akses dicabut, sisa data
          yang belum terkirim tidak akan pernah sampai.
        </p>

        <label className="formfield">
          <span className="formfield__label">Ketik {ship.slug} untuk mengonfirmasi</span>
          <input
            ref={fieldRef}
            className="formfield__input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
          />
        </label>

        {decommission.error && (
          <FormError
            error={decommission.error}
            forbidden="Akun Anda tidak punya izin menonaktifkan kapal."
          />
        )}

        <div className="modal__actions">
          <button type="button" className="btn" onClick={onClose}>
            Batal
          </button>
          <button
            type="submit"
            className="btn btn--danger-solid"
            disabled={!confirmed || decommission.isPending}
          >
            {decommission.isPending ? "Menonaktifkan…" : "Nonaktifkan kapal"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
