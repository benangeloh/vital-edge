"""/api/v1/telemetry — jalur baca.

Terpisah dari bidang ingest secara sengaja: badai reconnect seluruh armada di
bidang ingest tidak boleh membuat dashboard tidak bisa dipakai. Keduanya punya
autentikasi, rate limit, dan SLO sendiri.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query

from fleetview_central.http.envelope import success
from fleetview_central.platform.deps import CurrentUser, Influx

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])

#: Bucket dipilih berdasarkan lebar jendela waktu, sehingga chart satu tahun
#: membaca data ter-downsample dan tetap cepat. Response menyebutkan bucket mana
#: yang menjawab, supaya chart bisa jujur melabeli dirinya "rata-rata per jam".
_WINDOW_BUCKETS = ((6 * 3600, "raw"), (7 * 86400, "1m"), (None, "1h"))

FUTURE_MARGIN = "5m"
"""Batas atas rentang query, sedikit di masa depan.

`range(start:-1h)` di Flux punya `stop: now()` implisit, sehingga titik dengan
timestamp lebih baru dari sekarang **tidak akan terlihat sama sekali**. Itu bukan
kasus hipotetis: jam kapal melenceng, dan kapal yang jamnya maju beberapa detik
akan tampak seolah berhenti mengirim data. Margin ini membuat data seperti itu
tetap muncul, sementara `clock_adjusted` pada tag-nya tetap menandai bahwa ada
yang perlu diperiksa.
"""


def _bucket_for(range_seconds: int) -> str:
    for limit, name in _WINDOW_BUCKETS:
        if limit is None or range_seconds <= limit:
            return name
    return "1h"


@router.get("/series", summary="Deret waktu satu atau beberapa sensor")
async def series(
    user: CurrentUser,
    influx: Influx,
    ship_id: UUID,
    sensor_id: Annotated[str | None, Query()] = None,
    range_seconds: Annotated[int, Query(ge=60, le=31_536_000)] = 3600,
    aggregate: Annotated[str, Query(pattern="^(mean|max|min|last)$")] = "mean",
) -> dict[str, Any]:
    _ = user
    bucket = _bucket_for(range_seconds)
    window = "10s" if bucket == "raw" else ("1m" if bucket == "1m" else "1h")

    flux = (
        f'from(bucket:"telemetry") |> range(start:-{range_seconds}s, stop:{FUTURE_MARGIN}) '
        f'|> filter(fn:(r) => r.ship_id == "{ship_id}")'
    )
    if sensor_id:
        flux += f' |> filter(fn:(r) => r.sensor_id == "{sensor_id}")'
    flux += f" |> aggregateWindow(every:{window}, fn:{aggregate}, createEmpty:false)"

    csv = await influx.query_csv(flux)
    return success(
        {"csv": csv, "bucket_used": bucket, "window": window, "aggregate": aggregate},
        downsampled=bucket != "raw",
    )


@router.get("/latest", summary="Nilai terakhir setiap sensor")
async def latest(user: CurrentUser, influx: Influx, ship_id: UUID) -> dict[str, Any]:
    _ = user
    flux = (
        f'from(bucket:"telemetry") |> range(start:-24h, stop:{FUTURE_MARGIN}) '
        f'|> filter(fn:(r) => r.ship_id == "{ship_id}") |> last()'
    )
    return success({"csv": await influx.query_csv(flux)})
