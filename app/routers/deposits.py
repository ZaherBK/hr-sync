"""
Points de terminaison de l'API pour les Avances (Dépôts).

Permet aux administrateurs et managers de créer et lister les avances sur salaire.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Importer les schémas et modèles nécessaires
from ..schemas import DepositCreate, DepositOut
from ..models import Deposit, Employee, Role
from ..auth import require_role
from ..deps import get_db, current_user
from ..audit import log # Pour enregistrer l'action dans le journal d'audit

router = APIRouter(prefix="/api/deposits", tags=["deposits"])


@router.post("/", response_model=DepositOut, status_code=201,
             dependencies=[Depends(require_role(Role.admin, Role.manager))])
async def create_deposit(
    payload: DepositCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user), # Obtenir l'utilisateur actuel pour created_by et audit log
) -> DepositOut:
    """
    Créer une nouvelle avance pour un employé.
    Accessible par les admins et les managers.
    """
    # Vérifier si l'employé existe (optionnel mais recommandé)
    res_emp = await db.execute(select(Employee).where(Employee.id == payload.employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employé avec ID {payload.employee_id} non trouvé.")

    # Vérifier si le manager crée une avance pour un employé de son magasin (si nécessaire)
    # Note: L'admin peut créer pour n'importe qui.
    if user.role == Role.manager and employee.branch_id != user.branch_id:
         raise HTTPException(status_code=403, detail="Les managers ne peuvent créer des avances que pour les employés de leur propre magasin.")

    deposit = Deposit(
        employee_id=payload.employee_id,
        amount=payload.amount, # Le montant est déjà validé par Pydantic (Decimal > 0)
        date=payload.date,
        note=payload.note,
        created_by=user.id, # Enregistrer qui a créé l'avance
    )
    db.add(deposit)
    await db.commit()
    await db.refresh(deposit)

    # Enregistrer l'action dans le journal d'audit
    await log(
        db,
        actor_id=user.id,
        action="create",
        entity="deposit",
        entity_id=deposit.id,
        branch_id=employee.branch_id, # Utiliser le branch_id de l'employé concerné
        details=f"Employé ID={payload.employee_id}, Montant={payload.amount}, Date={payload.date}",
    )

    # Retourner l'objet Deposit créé validé par le schéma DepositOut
    # Pydantic gère la conversion Decimal -> float/str pour JSON si nécessaire
    return DepositOut.model_validate(deposit)


@router.get("/", response_model=list[DepositOut],
            dependencies=[Depends(require_role(Role.admin, Role.manager))])
async def list_deposits(
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user), # Obtenir l'utilisateur pour filtrer par magasin si manager
    employee_id: int | None = None # Paramètre optionnel pour filtrer par employé
) -> list[DepositOut]:
    """
    Lister toutes les avances.
    Les managers ne voient que les avances des employés de leur magasin.
    Les admins voient tout.
    Possibilité de filtrer par employee_id.
    """
    stmt = select(Deposit).order_by(Deposit.date.desc(), Deposit.created_at.desc())

    # Filtrer par magasin si l'utilisateur est un manager
    if user.role == Role.manager:
        # Il faut joindre avec Employee pour accéder à branch_id
        stmt = stmt.join(Deposit.employee).where(Employee.branch_id == user.branch_id)

    # Filtrer par employé si employee_id est fourni
    if employee_id is not None:
        stmt = stmt.where(Deposit.employee_id == employee_id)

    res = await db.execute(stmt)
    deposits = res.scalars().all()

    # Convertir les objets ORM en schémas Pydantic pour la réponse
    return [DepositOut.model_validate(d) for d in deposits]
