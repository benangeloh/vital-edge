import { useCallback, useEffect, useRef, useState } from "react";
import { ApiFailure } from "@/lib/api";
import {
  buildInstallScript,
  installScriptFileName,
  isReachableFromShip,
} from "@/lib/installScript";
import { useOnboardShip, type OnboardResult } from "@/lib/queries";
import { nextShipCode } from "@/lib/shipCode";

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
        aria-labelledby="onboard-title"
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

function CredentialHandover({
  result,
  onDone,
}: {
  result: OnboardResult;
  onDone: () => void;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [centralUrl, setCentralUrl] = useState(() => window.location.origin);
  const [panelHost, setPanelHost] = useState("");

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

  const fileName = installScriptFileName(result.slug);
  const urlValid = /^https?:\/\/\S+$/i.test(centralUrl.trim());
  const urlLocalOnly = urlValid && !isReachableFromShip(centralUrl.trim());

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

  const download = () => {
    const script = buildInstallScript({
      shipName: result.name,
      slug: result.slug,
      centralUrl: centralUrl.trim().replace(/\/+$/, ""),
      clientId: result.client_id,
      secret: result.client_secret,
      panelHost: panelHost.trim(),
    });
    const url = URL.createObjectURL(new Blob([script], { type: "text/x-shellscript" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.click();
    URL.revokeObjectURL(url);
    // Skripnya memuat kredensial, jadi mengunduh sama artinya dengan menyimpannya.
    setAcknowledged(true);
  };

  return (
    <div>
      <h2 className="modal__title" id="onboard-title">
        {result.name} terdaftar
      </h2>
      <p className="modal__warn" role="alert">
        Rahasia di bawah <strong>hanya ditampilkan sekali</strong>. Di server ia hanya
        tersimpan sebagai hash dan tidak bisa dibaca kembali. Simpan sekarang; kalau hilang,
        terbitkan kredensial baru lalu cabut yang lama.
      </p>

      <section className="autoinstall" aria-labelledby="autoinstall-title">
        <h3 className="autoinstall__title" id="autoinstall-title">
          Pasang otomatis dengan satu skrip
        </h3>
        <p className="autoinstall__desc">
          Skrip ini sudah berisi kredensial kapal. Jalankan di komputer kapal, dan semuanya
          terpasang lalu terhubung ke pusat tanpa ada yang perlu diisi.
        </p>

        <label className="formfield">
          <span className="formfield__label">Alamat pusat</span>
          <input
            className="formfield__input"
            value={centralUrl}
            onChange={(e) => setCentralUrl(e.target.value)}
            inputMode="url"
            autoComplete="off"
          />
          <span className="formfield__hint">
            Alamat server pusat yang bisa dibuka dari kapal, misalnya https://fleetview.spil.co.id.
          </span>
        </label>
        {!urlValid && (
          <p className="formerror" role="alert">
            Alamat pusat harus diawali http:// atau https://.
          </p>
        )}
        {urlLocalOnly && (
          <p className="autoinstall__warn" role="status">
            Alamat ini hanya dikenal di komputer ini dan tidak akan bisa dijangkau dari kapal.
            Ganti dengan alamat server pusat sebelum mengunduh.
          </p>
        )}

        <label className="formfield">
          <span className="formfield__label">Alamat IP panel LP-A104</span>
          <input
            className="formfield__input"
            value={panelHost}
            onChange={(e) => setPanelHost(e.target.value)}
            placeholder="192.168.100.100"
            inputMode="decimal"
            autoComplete="off"
          />
          <span className="formfield__hint">
            Alamat panel di jaringan kapal. Bila dikosongkan, agent berjalan dalam mode
            simulasi dan datanya bukan data asli.
          </span>
        </label>

        <button type="button" className="btn btn--primary" onClick={download} disabled={!urlValid}>
          Unduh skrip pemasangan
        </button>

        <ol className="autoinstall__steps">
          <li>Salin berkas ke komputer kapal, misalnya lewat USB atau scp.</li>
          <li>
            Jalankan <code>sudo bash {fileName}</code>
          </li>
          <li>Tunggu sampai muncul tulisan "Pemasangan selesai". Skrip menghapus dirinya sendiri.</li>
        </ol>
        <p className="autoinstall__note">
          Untuk Linux: Ubuntu, Debian, dan turunannya seperti Raspberry Pi OS. Windows belum
          didukung.
        </p>
      </section>

      <details className="manual">
        <summary>Cara manual, tanpa skrip</summary>
        <SnippetBlock
          title="1. /etc/fleetview/secrets.env"
          hint="Rahasia. Batasi akses berkasnya (di Linux: chmod 0600) dan jangan dikirim lewat chat atau email"
          text={secretsEnv}
          onCopy={() => copy(secretsEnv, "secrets")}
          copied={copied === "secrets"}
          secret
        />
        <SnippetBlock
          title="2. /etc/fleetview/edge.yaml"
          hint="Identitas kapal, bukan rahasia"
          text={shipYaml}
          onCopy={() => copy(shipYaml, "yaml")}
          copied={copied === "yaml"}
        />
        {copied === "gagal" && (
          <p className="formerror" role="status">
            Browser menolak akses papan klip. Salin manual dari kotak di atas.
          </p>
        )}
      </details>

      <div className="nextsteps">
        <p className="nextsteps__title">Setelah terpasang</p>
        <ol className="nextsteps__list">
          <li>
            Buka Edge Console di komputer kapal, halaman Sensor, lalu tambahkan sensor yang
            terpasang di kapal.
          </li>
          <li>
            Kapal ini berstatus Online di dashboard begitu data pertamanya sampai, dan datanya
            bisa dilihat di tab Logger.
          </li>
        </ol>
      </div>

      <label className="checkfield">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
        />
        <span>Saya sudah menyimpan skrip atau kredensialnya</span>
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
