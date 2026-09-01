import { useCallback, useEffect, useRef, useState } from "react";
import { ApiFailure } from "@/lib/api";
import { useOnboardShip, type OnboardResult } from "@/lib/queries";

/**
 * Onboarding kapal baru tanpa menyentuh terminal.
 *
 * Dua tahap yang sengaja dipisah. Tahap pertama formulir; tahap kedua
 * menampilkan kredensial yang **hanya muncul sekali** — di basis data ia hanya
 * ada sebagai hash Argon2id, jadi tidak ada cara membacanya kembali nanti.
 * Menggabungkan keduanya dalam satu layar membuat rahasia itu mudah tertutup
 * tanpa sengaja sebelum sempat disalin.
 */
export function OnboardShipDialog({ onClose }: { onClose: () => void }) {
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
          imo_number: String(form.get("imo") ?? "").trim() || undefined,
          device_name: String(form.get("device") ?? "").trim() || undefined,
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
        aria-labelledby="onboard-title"
        ref={dialogRef}
      >
        {result === null ? (
          <form onSubmit={submit}>
            <h2 className="modal__title" id="onboard-title">
              Tambah kapal
            </h2>
            <p className="modal__desc">
              Mendaftarkan kapal, perangkat Edge, dan kredensialnya sekaligus.
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
            </label>

            <label className="formfield">
              <span className="formfield__label">Kode kapal</span>
              <input
                className="formfield__input"
                name="slug"
                required
                maxLength={64}
                pattern="[A-Za-z0-9\-]+"
                placeholder="SHIP-071"
                style={{ textTransform: "uppercase" }}
              />
              <span className="formfield__hint">
                Dipakai sebagai nama folder ekspor USB. Tidak bisa diubah setelah kapal
                beroperasi — paket USB lama akan berhenti cocok.
              </span>
            </label>

            <label className="formfield">
              <span className="formfield__label">
                Nomor IMO <span className="formfield__opt">opsional</span>
              </span>
              <input className="formfield__input" name="imo" maxLength={16} placeholder="9123456" />
            </label>

            <label className="formfield">
              <span className="formfield__label">
                Nama perangkat <span className="formfield__opt">opsional</span>
              </span>
              <input className="formfield__input" name="device" placeholder="Edge Pi #1" />
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

function CredentialHandover({
  result,
  onDone,
}: {
  result: OnboardResult;
  onDone: () => void;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);

  const secretsEnv = [
    `FLEETVIEW_SYNC__DEVICE_CLIENT_ID=${result.client_id}`,
    `FLEETVIEW_SYNC__DEVICE_SECRET=${result.client_secret}`,
  ].join("\n");

  const shipYaml = [
    "ship:",
    `  ship_id: "${result.ship_id}"`,
    `  ship_name: "${result.name}"`,
    `  device_id: "${result.device_id}"`,
  ].join("\n");

  const copy = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      window.setTimeout(() => setCopied(null), 2000);
    } catch {
      // Clipboard bisa ditolak browser (bukan konteks aman, atau izin dicabut).
      // Teksnya tetap tampil dan bisa disalin manual, jadi ini bukan kegagalan
      // yang perlu menghentikan apa pun.
      setCopied("gagal");
      window.setTimeout(() => setCopied(null), 2000);
    }
  };

  return (
    <div>
      <h2 className="modal__title" id="onboard-title">
        {result.name} terdaftar
      </h2>
      <p className="modal__warn" role="alert">
        Rahasia di bawah <strong>hanya ditampilkan sekali</strong>. Di server ia hanya
        tersimpan sebagai hash dan tidak bisa dibaca kembali. Salin sekarang ke
        Raspberry Pi kapal; kalau hilang, terbitkan kredensial baru lalu cabut yang lama.
      </p>

      <SnippetBlock
        title="1. /etc/fleetview/secrets.env"
        hint="chmod 0600 — jangan dikirim lewat chat atau email"
        text={secretsEnv}
        onCopy={() => copy(secretsEnv, "secrets")}
        copied={copied === "secrets"}
        secret
      />

      <SnippetBlock
        title="2. /etc/fleetview/edge.yaml"
        hint="identitas kapal; bukan rahasia"
        text={shipYaml}
        onCopy={() => copy(shipYaml, "yaml")}
        copied={copied === "yaml"}
      />

      {copied === "gagal" && (
        <p className="formerror" role="status">
          Browser menolak akses papan klip. Salin manual dari kotak di atas.
        </p>
      )}

      <label className="checkfield">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
        />
        <span>Saya sudah menyalin kredensialnya</span>
      </label>

      <div className="modal__actions">
        <button
          type="button"
          className="btn btn--primary"
          onClick={onDone}
          disabled={!acknowledged}
        >
          Selesai
        </button>
      </div>
    </div>
  );
}

function SnippetBlock({
  title,
  hint,
  text,
  onCopy,
  copied,
  secret = false,
}: {
  title: string;
  hint: string;
  text: string;
  onCopy: () => void;
  copied: boolean;
  secret?: boolean;
}) {
  return (
    <div className={secret ? "snippet snippet--secret" : "snippet"}>
      <div className="snippet__head">
        <div>
          <p className="snippet__title">{title}</p>
          <p className="snippet__hint">{hint}</p>
        </div>
        <button type="button" className="btn btn--small" onClick={onCopy}>
          {copied ? "Tersalin" : "Salin"}
        </button>
      </div>
      <pre className="snippet__body">{text}</pre>
    </div>
  );
}
