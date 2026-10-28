/** Peran yang boleh mengelola armada (mengubah, membuka kredensial, menonaktifkan).
 *  Sama dengan yang ditegakkan server; ini hanya menyembunyikan tombol yang pasti
 *  ditolak, bukan pengganti pemeriksaan di server. */
const FLEET_MANAGERS = ["admin", "fleet_manager"];

export function canManageFleet(roles: readonly string[] | undefined): boolean {
  return roles?.some((r) => FLEET_MANAGERS.includes(r)) ?? false;
}
