"""
Utilitaires de journalisation d'audit.
"""
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

# --- MODIFIÉ : Role n'est plus nécessaire ici ---
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
    """Enregistrer une entrée dans le journal d'audit."""
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
    #
    # --- 1. CORRECTION ICI ---
    #
    user_is_admin: bool = False, # Accepte un booléen, pas user_role: str
    #
    # --- FIN CORRECTION ---
    #
    branch_id: int | None = None,
    entity_types: list[str] | None = None # --- NOUVEAU: Filtre par type d'entité ---
) -> list[AuditOut]:
    """
    Retourne les entrées d'audit les plus récentes jusqu'à `limit`.
    Filtre par branch_id si l'utilisateur n'est pas admin.
    Filtre par entity_types si fourni (pour la page Paramètres).
    """
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())

    #
    # --- 2. CORRECTION ICI ---
    #
    # Les managers (non-admins) ne voient que les logs de LEUR magasin
    if not user_is_admin and branch_id is not None:
        stmt = stmt.where(AuditLog.branch_id == branch_id)
    # L'admin (user_is_admin=True) voit tout, donc pas de filtre.
    #
    # --- FIN CORRECTION ---
    #

    # --- FILTRAGE PAR TYPE D'ENTITÉ (pour la page Paramètres) ---
    if entity_types:
        stmt = stmt.where(AuditLog.entity.in_(entity_types))

    # Appliquer la limite
    stmt = stmt.limit(limit)

    res = await session.execute(stmt)

    return [AuditOut.model_validate(x) for x in res.scalars().all()]
