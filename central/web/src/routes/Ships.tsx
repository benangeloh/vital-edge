import { useEffect, useMemo, useState } from "react";
import { DecommissionShipDialog } from "@/components/DecommissionShipDialog";
import { EditShipDialog } from "@/components/EditShipDialog";
import { OnboardShipDialog } from "@/components/OnboardShipDialog";
import { PAGE_SIZE, Pager } from "@/components/Pager";
import { ShipCredentialDialog } from "@/components/ShipCredentialDialog";
import { ShipTable, type ShipActions } from "@/components/ShipTable";
import { ErrorState, Loading, Section } from "@/components/ui/Primitives";
import { canManageFleet } from "@/lib/permissions";
import { useFleet, useMe } from "@/lib/queries";
import { sortShips } from "@/lib/status";
import type { FleetShip } from "@/lib/types";

type ManageDialog = { kind: "credential" | "edit" | "decommission"; ship: FleetShip };

/** Daftar kapal dan tempat menambah kapal baru. */
export function ShipsPage({ search }: { search: string }) {
  const { data: ships, isLoading, error, refetch } = useFleet();
  const { data: me } = useMe();
  const [onboarding, setOnboarding] = useState(false);
  const [managing, setManaging] = useState<ManageDialog | null>(null);
  const [page, setPage] = useState(0);
  const now = Date.now();

  const actions: ShipActions | undefined = canManageFleet(me?.roles)
    ? {
        onCredential: (ship) => setManaging({ kind: "credential", ship }),
        onEdit: (ship) => setManaging({ kind: "edit", ship }),
        onDecommission: (ship) => setManaging({ kind: "decommission", ship }),
      }
    : undefined;

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
        <ShipTable ships={visible} now={now} actions={actions} />
        <Pager page={current} total={filtered.length} onPage={setPage} />
      </Section>
      {managing?.kind === "credential" && (
        <ShipCredentialDialog ship={managing.ship} onClose={() => setManaging(null)} />
      )}
      {managing?.kind === "edit" && (
        <EditShipDialog ship={managing.ship} onClose={() => setManaging(null)} />
      )}
      {managing?.kind === "decommission" && (
        <DecommissionShipDialog ship={managing.ship} onClose={() => setManaging(null)} />
      )}
      {onboarding && (
        <OnboardShipDialog
          onClose={() => setOnboarding(false)}
          existingSlugs={(ships ?? []).map((s) => s.slug)}
        />
      )}
    </>
  );
}
