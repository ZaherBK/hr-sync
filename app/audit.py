"""
Audit logging utilities.

Provides functions to record actions taken in the system and to retrieve the
latest audit log entries for display on the dashboard.
"""
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog, Role
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


async def latest(
    session: AsyncSession,
    limit: int = 50,
    user_role: str | None = None, # <-- Ajouté
    branch_id: int | None = None   # <-- Ajouté
) -> list[AuditOut]:
    """
    Retourne les entrées d'audit les plus récentes jusqu'à `limit`.
    Filtre par branch_id si l'utilisateur est un manager.
    """
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())

    # --- AJOUT DU FILTRAGE ---
    # Si l'utilisateur est un manager et a un branch_id, filtrer les logs
    if user_role == Role.manager.value and branch_id is not None:
        # On ne montre que les logs liés à son magasin OU les logs globaux (sans branch_id)
        # OU les logs où il est l'acteur (actor_id)
        stmt = stmt.where(
            (AuditLog.branch_id == branch_id) |
            (AuditLog.branch_id.is_(None)) |
            (AuditLog.actor_id == session.info.get('user_id')) # Besoin de passer user_id ou récupérer autrement
            # Alternative: Filtrer strictement par branch_id si c'est la règle métier
            # stmt = stmt.where(AuditLog.branch_id == branch_id)
        )
    # L'admin (user_role == 'admin') voit tout, donc pas de filtre supplémentaire.

    # Appliquer la limite
    stmt = stmt.limit(limit)
    # --- FIN DU FILTRAGE ---

    res = await session.execute(stmt)
    # Passer user_id à la session info si besoin dans le filtre ci-dessus
    # (Pour l'instant, le filtre actor_id est commenté car user_id n'est pas passé simplement ici)

    return [AuditOut.model_validate(x) for x in res.scalars().all()]
