import { ApiFailure } from "@/lib/api";
import { useIssueShipCredential, useShipCredential } from "@/lib/queries";
import type { FleetShip } from "@/lib/types";
import { CredentialHandover } from "./CredentialHandover";
import { FormError } from "./ui/FormError";
import { Modal } from "./ui/Modal";

/** Kode error server yang artinya "rahasia lama tidak bisa dibuka", bukan "gagal":
 *  jalan keluarnya menerbitkan kredensial baru. */
const NEEDS_NEW_CREDENTIAL = new Set([
  "fleet.credential_not_recoverable",
  "fleet.no_active_credential",
  "fleet.credential_unreadable",
]);

/** Membuka kembali kredensial dan skrip pasang kapal yang sudah terdaftar. */
export function ShipCredentialDialog({
  ship,
  onClose,
}: {
  ship: FleetShip;
  onClose: () => void;
}) {
  const opened = useShipCredential(ship.ship_id);
  const issue = useIssueShipCredential(ship.ship_id);

  if (issue.data) {
    return (
      <Modal titleId="credential-title" onClose={onClose} dismissable={false}>
        <CredentialHandover
          result={issue.data}
          title={`Kredensial baru untuk ${issue.data.name}`}
          doneLabel="Selesai"
          onDone={onClose}
        />
      </Modal>
    );
  }

  if (opened.data) {
    return (
      <Modal titleId="credential-title" onClose={onClose} dismissable={false}>
        <CredentialHandover
          result={opened.data}
          title={`Kredensial ${opened.data.name}`}
          doneLabel="Tutup"
          onDone={onClose}
        />
      </Modal>
    );
  }

  const failure = opened.error instanceof ApiFailure ? opened.error : null;
  const needsNew = failure !== null && NEEDS_NEW_CREDENTIAL.has(failure.error.code);

  return (
    <Modal titleId="credential-title" onClose={onClose}>
      <h2 className="modal__title" id="credential-title">
        Kredensial {ship.name}
      </h2>

      {opened.isPending && (
        <p className="modal__desc" role="status">
          Membuka kredensial…
        </p>
      )}

      {needsNew && (
        <>
          <p className="modal__warn">{failure.error.message}</p>
          <p className="modal__desc">
            Kredensial yang sudah dipasang di komputer kapal tidak otomatis dicabut, jadi kapal
            yang sedang berjalan tetap terhubung. Cabut yang lama sendiri setelah kapal memakai
            yang baru.
          </p>
        </>
      )}

      {opened.error && !needsNew && (
        <FormError error={opened.error} forbidden="Akun Anda tidak punya izin membuka kredensial." />
      )}
      {issue.error && (
        <FormError error={issue.error} forbidden="Akun Anda tidak punya izin menerbitkan kredensial." />
      )}

      <div className="modal__actions">
        <button type="button" className="btn" onClick={onClose}>
          Tutup
        </button>
        {needsNew && (
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => issue.mutate()}
            disabled={issue.isPending}
          >
            {issue.isPending ? "Menerbitkan…" : "Terbitkan kredensial baru"}
          </button>
        )}
      </div>
    </Modal>
  );
}
