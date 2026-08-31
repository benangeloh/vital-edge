"""Layanan operasional: audit log dan metadata alert."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleetview_central.modules.ops.models import AlertEvent, AlertRule, AuditLog
from fleetview_common import now_utc

__all__ = ["AlertService", "AuditService"]


class AuditService:
    """Audit log bersifat append-only: tidak pernah diubah, tidak pernah dihapus."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def record(
        self,
        *,
        actor_type: str,
        action: str,
        actor_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        detail: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> None:
        self._db.add(
            AuditLog(
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                detail=detail,
                ip_address=ip_address,
            )
        )
        await self._db.flush()

    async def list_recent(self, *, limit: int = 100) -> list[AuditLog]:
        return list(
            await self._db.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
            )
        )


class AlertService:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def list_rules(self, ship_id: UUID | None = None) -> list[AlertRule]:
        stmt = select(AlertRule).where(AlertRule.is_active.is_(True))
        if ship_id is not None:
            stmt = stmt.where(AlertRule.ship_id.in_([ship_id, None]))
        return list(await self._db.scalars(stmt))

    async def create_rule(
        self,
        *,
        name: str,
        metric: str,
        comparison: str,
        threshold: float,
        ship_id: UUID | None = None,
        sensor_id: str | None = None,
        severity: str = "warning",
    ) -> AlertRule:
        rule = AlertRule(
            name=name,
            metric=metric,
            comparison=comparison,
            threshold=threshold,
            ship_id=ship_id,
            sensor_id=sensor_id,
            severity=severity,
        )
        self._db.add(rule)
        await self._db.flush()
        return rule

    async def list_events(
        self,
        *,
        ship_id: UUID | None = None,
        severity: str | None = None,
        unacknowledged_only: bool = False,
        limit: int = 100,
    ) -> list[AlertEvent]:
        stmt = select(AlertEvent).order_by(AlertEvent.occurred_at.desc()).limit(limit)
        if ship_id is not None:
            stmt = stmt.where(AlertEvent.ship_id == ship_id)
        if severity is not None:
            stmt = stmt.where(AlertEvent.severity == severity)
        if unacknowledged_only:
            stmt = stmt.where(AlertEvent.acknowledged_at.is_(None))
        return list(await self._db.scalars(stmt))

    async def acknowledge(self, event_id: UUID, user_id: UUID) -> AlertEvent | None:
        event: AlertEvent | None = await self._db.get(AlertEvent, event_id)
        if event is None:
            return None
        event.acknowledged_at = now_utc()
        event.acknowledged_by = user_id
        await self._db.flush()
        return event
