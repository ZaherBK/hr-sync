"""
Audit logging utilities.

Provides functions to record actions taken in the system and to retrieve the
latest audit log entries for display on the dashboard.
"""
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog
from .schemas import AuditOut


async def log(
    session: AsyncSession,
    actor_id: int,
    action: str,
    entity: str,
    entity_id: int | None,
    branch_id: int | None,
    details: str | None = None,
) -> None:
    """Record an audit log entry."""
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            branch_id=branch_id,
            details=details,
        )
    )
    await session.commit()


async def latest(session: AsyncSession, limit: int = 50) -> list[AuditOut]:
    """Return the most recent audit entries up to `limit`."""
    res = await session.execute(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit))
    return [AuditOut.model_validate(x) for x in res.scalars().all()]