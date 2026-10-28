import { ApiFailure } from "@/lib/api";

/** Kegagalan dari server. Pesannya dipakai apa adanya bila ada, karena ia sudah
 *  menjelaskan persoalannya ("nomor IMO sudah dipakai kapal lain") jauh lebih baik
 *  daripada pesan generik. Kekurangan izin diganti kalimat yang bisa ditindaklanjuti. */
export function FormError({
  error,
  forbidden,
}: {
  error: unknown;
  /** Kalimat saat peran pemakai tidak cukup. */
  forbidden: string;
}) {
  const failure = error instanceof ApiFailure ? error : null;
  const message =
    failure?.error.message ?? (error instanceof Error ? error.message : "Permintaan gagal");
  return (
    <p className="formerror" role="alert">
      {failure?.status === 403 ? forbidden : message}
    </p>
  );
}
