"""/api/v1/alerts dan /api/v1/health."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from fleetview_central.http.envelope import success
from fleetview_central.modules.ops.service import AlertService, AuditService
from fleetview_central.platform.deps import CurrentUser, DbSession, Influx
from fleetview_common import ValidationError

router = APIRouter(prefix="/api/v1", tags=["ops"])


class CreateRule(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    metric: Annotated[str, Field(min_length=1, max_length=64)]
    comparison: Annotated[str, Field(pattern="^(gt|gte|lt|lte|eq)$")]
    threshold: float
    ship_id: UUID | None = None
    sensor_id: str | None = None
    severity: Annotated[str, Field(pattern="^(info|warning|critical)$")] = "warning"


@router.get("/health", summary="Kesehatan platform", tags=["health"])
async def health(db: DbSession, influx: Influx) -> dict[str, Any]:
    """Readiness sungguhan: memeriksa Postgres dan InfluxDB.

    Berbeda dari `/api/health` yang tanpa dependency — endpoint itu harus tetap
    menjawab justru saat dependency sedang bermasalah.
    """
    checks: dict[str, Any] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"gagal: {exc}"

    checks["influxdb"] = "ok" if await influx.healthy() else "gagal"
    healthy = all(v == "ok" for v in checks.values())
    return success({"status": "healthy" if healthy else "degraded", "checks": checks})


@router.get("/alerts", summary="Daftar kejadian alert")
async def list_alerts(
    user: CurrentUser,
    db: DbSession,
    ship_id: UUID | None = None,
    severity: Annotated[str | None, Query(pattern="^(info|warning|critical)$")] = None,
    unacknowledged: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    _ = user
    events = await AlertService(db).list_events(
        ship_id=ship_id,
        severity=severity,
        unacknowledged_only=unacknowledged,
        limit=limit,
    )
    return success(
        [
            {
                "id": str(e.id),
                "ship_id": str(e.ship_id),
                "sensor_id": e.sensor_id,
                "severity": e.severity,
                "message": e.message,
                "value": e.value,
                "occurred_at": e.occurred_at.isoformat(),
                "acknowledged_at": e.acknowledged_at.isoformat() if e.acknowledged_at else None,
            }
            for e in events
        ]
    )


@router.post("/alerts/rules", status_code=201, summary="Buat aturan alert")
async def create_rule(body: CreateRule, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    user.require_role("admin", "fleet_manager")
    rule = await AlertService(db).create_rule(
        name=body.name,
        metric=body.metric,
        comparison=body.comparison,
        threshold=body.threshold,
        ship_id=body.ship_id,
        sensor_id=body.sensor_id,
        severity=body.severity,
    )
    return success({"rule_id": str(rule.id), "name": rule.name})


@router.post("/alerts/{event_id}/acknowledge", summary="Akui sebuah alert")
async def acknowledge(event_id: UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    event = await AlertService(db).acknowledge(event_id, user.user_id)
    if event is None:
        raise ValidationError(f"alert {event_id} tidak ditemukan", code="alerts.not_found")
    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="alerts.acknowledged",
        resource_type="alert",
        resource_id=str(event_id),
    )
    return success(
        {
            "id": str(event.id),
            "acknowledged_at": event.acknowledged_at.isoformat() if event.acknowledged_at else None,
        }
    )


@router.get("/audit", summary="Audit log terbaru", tags=["audit"])
async def audit(user: CurrentUser, db: DbSession, limit: int = 100) -> dict[str, Any]:
    user.require_role("admin", "auditor")
    logs = await AuditService(db).list_recent(limit=limit)
    return success(
        [
            {
                "id": str(a.id),
                "actor_type": a.actor_type,
                "actor_id": str(a.actor_id) if a.actor_id else None,
                "action": a.action,
                "resource_type": a.resource_type,
                "resource_id": a.resource_id,
                "detail": a.detail,
                "created_at": a.created_at.isoformat(),
            }
            for a in logs
        ]
    )
