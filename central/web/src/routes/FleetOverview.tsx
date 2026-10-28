import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FleetStats } from "@/components/FleetStats";
import { PAGE_SIZE, Pager } from "@/components/Pager";
import { ShipTable } from "@/components/ShipTable";
import { RefreshIcon } from "@/components/shell/nav-icons";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ErrorState, Loading, Section } from "@/components/ui/Primitives";
import { attentionNote } from "@/lib/narrative";
import { useFleet } from "@/lib/queries";
import {
  STATUS_META,
  needsAttention,
  shipStatus,
  sortShips,
  summarise,
  type ShipStatus,
} from "@/lib/status";

const ATTENTION_LIMIT = 5;

export function FleetOverview({ search }: { search: string }) {
  const { data: ships, isLoading, error, refetch, dataUpdatedAt, isFetching } = useFleet();
  const [status, setStatus] = useState<ShipStatus | "all">("all");
  const [page, setPage] = useState(0);
  const now = Date.now();

  const summary = useMemo(() => summarise(ships ?? [], now), [ships, now]);
  const attention = useMemo(() => needsAttention(ships ?? [], now), [ships, now]);

  const filtered = useMemo(() => {
    let list = ships ?? [];
    if (status !== "all") list = list.filter((s) => shipStatus(s, now) === status);
    if (search.trim()) {
      const needle = search.trim().toLowerCase();
      list = list.filter(
        (s) =>
          s.name.toLowerCase().includes(needle) ||
          s.slug.toLowerCase().includes(needle) ||
          s.ship_id.toLowerCase().includes(needle),
      );
    }
    return sortShips(list, now);
  }, [ships, status, search, now]);

  // Saringan berubah, kembali ke halaman pertama; kalau tidak, halaman yang
  // dipilih bisa melewati ujung daftar yang baru dan tabelnya tampak kosong.
  useEffect(() => setPage(0), [status, search]);

  if (isLoading) return <Loading label="Memuat armada" />;
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;

  const total = ships?.length ?? 0;
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const visible = filtered.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE);
  const hiddenAttention = Math.max(0, attention.length - ATTENTION_LIMIT);

  return (
    <>
      <div className="page-head page-head--split">
        <div>
          <h1 className="page-title">Ringkasan armada</h1>
          <p className="page-sub">Pantau kondisi seluruh kapal SPIL dan buka detail kapal yang perlu dicek.</p>
        </div>
        <div className="updated" role="status">
          <span className="updated__text">
            Diperbarui{" "}
            <span className="updated__time tabular">
              {dataUpdatedAt
                ? new Date(dataUpdatedAt).toLocaleTimeString("id-ID", {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })
                : "belum ada"}
            </span>
          </span>
          <button
            type="button"
            className={isFetching ? "updated__btn updated__btn--busy" : "updated__btn"}
            onClick={() => refetch()}
            aria-label="Muat ulang data armada"
            title="Muat ulang. Data diperbarui otomatis tiap 10 detik."
          >
            <RefreshIcon className="updated__icon" />
          </button>
        </div>
      </div>

      <FleetStats summary={summary} selected={status} onSelect={setStatus} />

      {/* Bagian ini hanya muncul kalau memang ada yang bermasalah. Panel
          "semua baik" yang selalu tampil justru melatih mata mengabaikannya. */}
      {attention.length > 0 && (
        <Section
          title="Perlu tindakan"
          description={`${attention.length} kapal tidak dalam kondisi normal. Yang paling gawat ada di atas.`}
        >
          <ul className="attention">
            {attention.slice(0, ATTENTION_LIMIT).map((ship) => {
              const meta = STATUS_META[shipStatus(ship, now)];
              return (
                <li key={ship.ship_id} className={`attention__row attention__row--${meta.tone}`}>
                  <div className="attention__who">
                    <Link to={`/ships/${ship.ship_id}`} className="attention__name">
                      {ship.name}
                    </Link>
                    <StatusBadge meta={meta} />
                  </div>
                  <p className="attention__note">{attentionNote(ship, now)}</p>
                  <Link
                    to={`/ships/${ship.ship_id}`}
                    className="attention__go"
                    aria-label={`Buka ${ship.name}`}
                  >
                    Buka kapal
                  </Link>
                </li>
              );
            })}
          </ul>
          {hiddenAttention > 0 && (
            <p className="attention__more">
              Masih ada {hiddenAttention} kapal lain yang perlu perhatian. Lihat di daftar di bawah.
            </p>
          )}
        </Section>
      )}

      <Section
        title="Daftar kapal"
        description={
          filtered.length === total
            ? `${total} kapal terdaftar. Yang perlu perhatian ada di atas.`
            : `Menampilkan ${filtered.length} dari ${total} kapal.`
        }
        actions={
          status !== "all" ? (
            <button type="button" className="btn btn--outline" onClick={() => setStatus("all")}>
              Tampilkan semua kapal
            </button>
          ) : undefined
        }
      >
        <ShipTable ships={visible} now={now} />
        <Pager page={current} total={filtered.length} onPage={setPage} />
      </Section>
    </>
  );
}
