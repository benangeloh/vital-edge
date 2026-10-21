import { useEffect, useMemo, useState } from "react";
import { OnboardShipDialog } from "@/components/OnboardShipDialog";
import { PAGE_SIZE, Pager } from "@/components/Pager";
import { ShipTable } from "@/components/ShipTable";
import { ErrorState, Loading, Section } from "@/components/ui/Primitives";
import { useFleet } from "@/lib/queries";
import { sortShips } from "@/lib/status";

/** Daftar kapal dan tempat menambah kapal baru. */
export function ShipsPage({ search }: { search: string }) {
  const { data: ships, isLoading, error, refetch } = useFleet();
  const [onboarding, setOnboarding] = useState(false);
  const [page, setPage] = useState(0);
  const now = Date.now();

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const list = needle
      ? (ships ?? []).filter(
          (s) =>
            s.name.toLowerCase().includes(needle) ||
            s.slug.toLowerCase().includes(needle) ||
            s.ship_id.toLowerCase().includes(needle),
        )
      : (ships ?? []);
    return sortShips(list, now);
  }, [ships, search, now]);

  useEffect(() => setPage(0), [search]);

  if (isLoading) return <Loading label="Memuat kapal" />;
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;

  const current = Math.min(page, Math.max(0, Math.ceil(filtered.length / PAGE_SIZE) - 1));
  const visible = filtered.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE);

  return (
    <>
      <div className="page-head page-head--split">
        <div>
          <h1 className="page-title">Kapal</h1>
          <p className="page-sub">
            {filtered.length} dari {ships?.length ?? 0} kapal
            {search ? ` cocok dengan "${search}"` : ""}. Tambahkan kapal baru di sini, lalu pasang
            kredensialnya di perangkat kapal.
          </p>
        </div>
        <button type="button" className="btn btn--primary btn--large" onClick={() => setOnboarding(true)}>
          Tambah kapal
        </button>
      </div>
      <Section title="Daftar kapal">
        <ShipTable ships={visible} now={now} />
        <Pager page={current} total={filtered.length} onPage={setPage} />
      </Section>
      {onboarding && (
        <OnboardShipDialog
          onClose={() => setOnboarding(false)}
          existingSlugs={(ships ?? []).map((s) => s.slug)}
        />
      )}
    </>
  );
}
