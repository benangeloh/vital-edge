"""/api/v1/telemetry dan /api/v1/sync — bidang ingest.

Telemetry diterima **per batch**, tidak pernah per titik sensor. Pada 70 kapal
dengan 80 sensor pada 1 Hz, request per titik berarti 5.600 request per detik
untuk data yang muat dalam beberapa puluh batch — dan tidak satu pun di antaranya
bisa dibuat idempoten dengan murah.
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel

from fleetview_central.http.envelope import success
from fleetview_central.modules.fleet.service import FleetService
from fleetview_central.modules.ingest.models import ShipSyncState
from fleetview_central.modules.ingest.service import IngestService
from fleetview_central.platform.deps import CurrentDevice, DbSession, Influx
from fleetview_common import ValidationError, get_logger, now_utc
from fleetview_contracts import BatchEnvelope, Heartbeat, HeartbeatResponse

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["ingest"])

MAX_PAYLOAD_BYTES = 32 * 1024 * 1024


class CommitRequest(BaseModel):
    payload_checksum: str


def _envelope_from_header(raw: str | None) -> BatchEnvelope:
    if not raw:
        raise ValidationError("header X-Batch-Envelope tidak ada", code="ingest.envelope_missing")
    try:
        return BatchEnvelope.model_validate(json.loads(raw))
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(
            f"X-Batch-Envelope tidak valid: {exc}", code="ingest.envelope_invalid"
        ) from exc


@router.post("/ingest/batches", summary="Kirim satu batch sekali jalan")
async def ingest_batch(
    request: Request,
    device: CurrentDevice,
    db: DbSession,
    influx: Influx,
    x_batch_envelope: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Untuk batch kecil. Idempoten: kiriman ulang memutar ulang ACK tersimpan."""
    envelope = _envelope_from_header(x_batch_envelope)
    payload = await request.body()
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValidationError(
            f"payload {len(payload)} byte melebihi batas {MAX_PAYLOAD_BYTES}",
            code="ingest.payload_too_large",
        )
    ack = await IngestService(db, influx).commit_batch(
        envelope,
        payload,
        ship_id=device.ship_id,
        transport="single",
    )
    return success(ack.model_dump(mode="json"))


@router.post("/ingest/sessions", summary="Buka atau lanjutkan upload berpotongan")
async def open_session(
    envelope: BatchEnvelope, device: CurrentDevice, db: DbSession, influx: Influx
) -> dict[str, Any]:
    """Mengembalikan potongan yang sudah diterima, sehingga edge hanya mengirim
    sisanya. Batch yang sudah committed dijawab dengan ACK-nya, tanpa satu byte
    pun dipindahkan."""
    data = await IngestService(db, influx).open_session(envelope, ship_id=device.ship_id)
    return success(data)


@router.put("/ingest/sessions/{session_id}/chunks/{index}", summary="Kirim satu potongan")
async def put_chunk(
    session_id: UUID,
    index: int,
    request: Request,
    device: CurrentDevice,
    db: DbSession,
    influx: Influx,
) -> Response:
    _ = device
    await IngestService(db, influx).store_chunk(session_id, index, await request.body())
    return Response(status_code=204)


@router.post("/ingest/sessions/{session_id}/commit", summary="Rakit dan commit")
async def commit_session(
    session_id: UUID,
    body: CommitRequest,
    device: CurrentDevice,
    db: DbSession,
    influx: Influx,
) -> dict[str, Any]:
    _ = body  # checksum sesungguhnya diverifikasi terhadap envelope tersimpan
    ack = await IngestService(db, influx).commit_session(session_id, ship_id=device.ship_id)
    return success(ack.model_dump(mode="json"))


@router.get("/ingest/sync-state", summary="Apa yang sudah dimiliki server")
async def ingest_sync_state(device: CurrentDevice, db: DbSession, influx: Influx) -> dict[str, Any]:
    state = await IngestService(db, influx).sync_state(device.ship_id)
    return success(state.model_dump(mode="json"))


@router.post("/ingest/heartbeat", summary="Tanda hidup dari kapal")
async def heartbeat(body: Heartbeat, device: CurrentDevice, db: DbSession) -> dict[str, Any]:
    """Dua arah: edge melaporkan kesehatannya, response membawa waktu server dan
    versi config yang menunggu. Dengan begitu edge tidak butuh koneksi masuk."""
    if body.ship_id != device.ship_id:
        raise ValidationError("heartbeat untuk kapal lain", code="ingest.ship_mismatch")

    state = await db.get(ShipSyncState, device.ship_id)
    if state is None:
        state = ShipSyncState(ship_id=device.ship_id)
        db.add(state)

    state.last_heartbeat_at = now_utc()
    state.connection_state = "online"
    state.pending_estimate = body.pending_records
    state.oldest_pending_age_seconds = body.oldest_pending_age_seconds
    state.active_transport = body.active_transport.value if body.active_transport else None
    state.agent_version = body.agent_version
    state.config_version = body.config_version
    state.edge_health = body.health.model_dump(mode="json") if body.health else None
    await db.flush()

    active = await FleetService(db).active_config(device.ship_id)
    return success(
        HeartbeatResponse(
            server_time=now_utc(),
            config_version=active.version if active else None,
        ).model_dump(mode="json")
    )


@router.get("/sync/ships/{ship_id}", summary="Status sync satu kapal", tags=["sync"])
async def ship_sync_status(ship_id: UUID, db: DbSession, influx: Influx) -> dict[str, Any]:
    state = await IngestService(db, influx).sync_state(ship_id)
    row = await db.get(ShipSyncState, ship_id)
    return success(
        {
            **state.model_dump(mode="json"),
            "has_gap": state.has_gap,
            "total_batches": row.total_batches if row else 0,
            "total_records": row.total_records if row else 0,
            "connection_state": row.connection_state if row else "offline",
            "pending_estimate": row.pending_estimate if row else None,
        }
    )
