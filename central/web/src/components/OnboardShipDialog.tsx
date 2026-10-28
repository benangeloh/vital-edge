import { useCallback, useEffect, useRef, useState } from "react";
import { ApiFailure } from "@/lib/api";
import { useOnboardShip, type OnboardResult } from "@/lib/queries";
import { nextShipCode } from "@/lib/shipCode";
import { CredentialHandover } from "./CredentialHandover";

/**
 * Onboarding kapal baru tanpa menyentuh terminal.
 *
 * Dua tahap yang sengaja dipisah. Tahap pertama formulir; tahap kedua
 * menampilkan kredensial yang **hanya muncul sekali** — di basis data ia hanya
 * ada sebagai hash Argon2id, jadi tidak ada cara membacanya kembali nanti.
 * Menggabungkan keduanya dalam satu layar membuat rahasia itu mudah tertutup
 * tanpa sengaja sebelum sempat disalin.
 */
export function OnboardShipDialog({
  onClose,
  existingSlugs = [],
}: {
  onClose: () => void;
  /** Kode kapal yang sudah dipakai, untuk menyarankan nomor berikutnya. */
  existingSlugs?: string[];
}) {
  const onboard = useOnboardShip();
  const [result, setResult] = useState<OnboardResult | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    firstFieldRef.current?.focus();
  }, []);

  // Escape hanya menutup selama kredensial belum tampil. Setelah itu tombolnya
  // harus ditekan sadar: menutup tanpa menyalin berarti harus menerbitkan ulang.
  const dismissable = result === null;
  useEffect(() => {
    if (!dismissable) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dismissable, onClose]);

  const submit = useCallback(
    (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      onboard.mutate(
        {
          name: String(form.get("name") ?? "").trim(),
          slug: String(form.get("slug") ?? "").trim().toUpperCase(),
        },
        { onSuccess: setResult },
      );
    },
    [onboard],
  );

  return (
    <div className="modal-backdrop" role="presentation">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={result === null ? "onboard-title" : "credential-title"}
        ref={dialogRef}
      >
        {result === null ? (
          <form onSubmit={submit}>
            <h2 className="modal__title" id="onboard-title">
              Tambah kapal
            </h2>
            <p className="modal__desc">
              Mendaftarkan kapal beserta perangkat Edge dan kredensialnya sekaligus.
            </p>

            <label className="formfield">
              <span className="formfield__label">Nama kapal</span>
              <input
                ref={firstFieldRef}
                className="formfield__input"
                name="name"
                required
                maxLength={128}
                placeholder="KM Sinar Jaya"
              />
              <span className="formfield__hint">Nama resmi kapal, seperti tertulis di dokumen kapal.</span>
            </label>

            <label className="formfield">
              <span className="formfield__label">Kode kapal</span>
              <input
                className="formfield__input"
                name="slug"
                required
                maxLength={64}
                pattern="[A-Za-z0-9\-]+"
                defaultValue={nextShipCode(existingSlugs)}
                style={{ textTransform: "uppercase" }}
              />
              <span className="formfield__hint">
                Kode internal SPIL, bentuknya SHIP-nomor. Sudah diisi nomor berikutnya yang masih
                kosong; ubah bila perlu, tetapi tidak boleh sama dengan kapal lain. Kode ini tidak
                bisa diganti setelah kapal beroperasi karena dipakai sebagai nama folder ekspor USB.
              </span>
            </label>

            {onboard.error && <OnboardError error={onboard.error} />}

            <div className="modal__actions">
              <button type="button" className="btn" onClick={onClose}>
                Batal
              </button>
              <button type="submit" className="btn btn--primary" disabled={onboard.isPending}>
                {onboard.isPending ? "Mendaftarkan…" : "Daftarkan kapal"}
              </button>
            </div>
          </form>
        ) : (
          <CredentialHandover result={result} onDone={onClose} />
        )}
      </div>
    </div>
  );
}

function OnboardError({ error }: { error: unknown }) {
  // Pesan dari server dipakai apa adanya bila ada: ia sudah menjelaskan
  // persoalannya ("slug sudah dipakai kapal lain") jauh lebih baik daripada
  // pesan generik yang harus ditebak sendiri artinya.
  const failure = error instanceof ApiFailure ? error : null;
  const message =
    failure?.error.message ??
    (error instanceof Error ? error.message : "Pendaftaran gagal");
  const forbidden = failure?.status === 403;
  return (
    <p className="formerror" role="alert">
      {forbidden ? "Akun Anda tidak punya izin menambah kapal." : message}
    </p>
  );
}
