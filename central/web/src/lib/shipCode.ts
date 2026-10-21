/** Kode kapal berikutnya yang masih kosong, berbentuk SHIP-NNN.
 *
 *  Dihitung dari nomor terbesar yang ada, bukan dari jumlah kapal: armada yang
 *  pernah menghapus atau melompati nomor tidak boleh membuat kode bertabrakan. */
export function nextShipCode(existing: string[]): string {
  const highest = existing.reduce((max, code) => {
    const match = /^SHIP-(\d+)$/i.exec(code.trim());
    return match ? Math.max(max, Number(match[1])) : max;
  }, 0);
  return `SHIP-${String(highest + 1).padStart(3, "0")}`;
}
