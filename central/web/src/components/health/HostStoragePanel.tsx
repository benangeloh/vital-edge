import { EmptyState, Field, Panel } from "@/components/ui/Primitives";
import { formatBytes } from "@/lib/format";
import type { HostStorage, ServerStorageLevel } from "@/lib/types";
import { type MeterLevel, StorageMeter } from "./StorageMeter";

const LEVEL_FROM_SERVER: Record<ServerStorageLevel, MeterLevel> = {
  ok: "ok",
  warning: "warn",
  critical: "crit",
};

export function HostStoragePanel({ storage }: { storage: HostStorage }) {
  const { disk, postgres_bytes } = storage;

  return (
    <Panel>
      {disk ? (
        <StorageMeter
          label="Disk host"
          percent={disk.used_percent}
          level={LEVEL_FROM_SERVER[disk.level]}
          detail={`${formatBytes(disk.free_bytes)} bebas dari ${formatBytes(disk.total_bytes)}`}
        />
      ) : (
        <EmptyState
          title="Pemakaian disk tidak dapat dibaca"
          hint="Server tidak bisa membaca partisi yang diperiksa. Periksa FLEETVIEW_HEALTH_DISK_PATH."
        />
      )}
      <dl className="fields">
        {disk && (
          <Field label="Path yang diperiksa">
            <code className="mono">{disk.path}</code>
          </Field>
        )}
        <Field label="Ukuran database Postgres">
          <span className="mono">{formatBytes(postgres_bytes)}</span>
        </Field>
      </dl>
    </Panel>
  );
}
