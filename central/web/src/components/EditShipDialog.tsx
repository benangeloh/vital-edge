import { useEffect, useRef } from "react";
import { FormError } from "./ui/FormError";
import { Modal } from "./ui/Modal";
import { useUpdateShip } from "@/lib/queries";
import type { FleetShip } from "@/lib/types";

/** Mengubah nama dan nomor IMO. Kode kapal tampil tetapi terkunci: ia dipakai
 *  sebagai nama folder ekspor USB dan pengenal di ledger. */
export function EditShipDialog({ ship, onClose }: { ship: FleetShip; onClose: () => void }) {
  const update = useUpdateShip(ship.ship_id);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    firstFieldRef.current?.focus();
  }, []);

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    update.mutate(
      {
        name: String(form.get("name") ?? "").trim(),
        imo_number: String(form.get("imo_number") ?? "").trim(),
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Modal titleId="edit-ship-title" onClose={onClose}>
      <form onSubmit={submit}>
        <h2 className="modal__title" id="edit-ship-title">
          Edit kapal
        </h2>
        <p className="modal__desc">Ubah data kapal. Kredensial dan datanya tidak berubah.</p>

        <label className="formfield">
          <span className="formfield__label">Nama kapal</span>
          <input
            ref={firstFieldRef}
            className="formfield__input"
            name="name"
            required
            maxLength={128}
            defaultValue={ship.name}
          />
        </label>

        <label className="formfield">
          <span className="formfield__label">Nomor IMO</span>
          <input
            className="formfield__input"
            name="imo_number"
            maxLength={16}
            defaultValue={ship.imo_number ?? ""}
          />
          <span className="formfield__hint">Boleh dikosongkan. Tidak boleh sama dengan kapal lain.</span>
        </label>

        <label className="formfield">
          <span className="formfield__label">Kode kapal</span>
          <input className="formfield__input" value={ship.slug} disabled readOnly />
          <span className="formfield__hint">
            Tidak bisa diganti karena dipakai sebagai nama folder ekspor USB.
          </span>
        </label>

        {update.error && (
          <FormError error={update.error} forbidden="Akun Anda tidak punya izin mengubah kapal." />
        )}

        <div className="modal__actions">
          <button type="button" className="btn" onClick={onClose}>
            Batal
          </button>
          <button type="submit" className="btn btn--primary" disabled={update.isPending}>
            {update.isPending ? "Menyimpan…" : "Simpan"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
