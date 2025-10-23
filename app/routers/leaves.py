"""
Points de terminaison de l'API pour la gestion des Congés.

Permet aux administrateurs de demander et approuver les congés des employés.
Fournit également la liste de toutes les demandes de congé.
Les managers ne peuvent PAS gérer les congés.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import LeaveCreate, LeaveOut
from ..models import Leave, Role, Employee # Importer Employee pour vérifier le magasin
from ..auth import require_role # Utilisé pour vérifier le rôle
from ..deps import get_db, current_user
from ..audit import log

router = APIRouter(prefix="/api/leaves", tags=["leaves"])


# MODIFIÉ : Seul l'admin peut créer une demande de congé
@router.post("/", response_model=LeaveOut, status_code=201,
             dependencies=[Depends(require_role(Role.admin))])
async def request_leave(
    payload: LeaveCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
) -> LeaveOut:
    """Créer une demande de congé pour un employé (Admin seulement)."""

    # Vérification optionnelle: l'employé existe ?
    res_emp = await db.execute(select(Employee).where(Employee.id == payload.employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employé avec ID {payload.employee_id} non trouvé.")

    # La validation start_date/end_date est faite dans le schéma Pydantic

    leave = Leave(
        employee_id=payload.employee_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        ltype=payload.ltype,
        approved=False, # Les congés ne sont pas approuvés par défaut
        created_by=user.id,
    )
    db.add(leave)
    await db.commit()
    await db.refresh(leave)

    # Enregistrer dans l'audit log
    await log(
        db,
        actor_id=user.id,
        action="create",
        entity="leave", # Congé
        entity_id=leave.id,
        branch_id=employee.branch_id, # Magasin de l'employé
        details=f"Employé ID={payload.employee_id} Dates={payload.start_date}->{payload.end_date} Type={payload.ltype.value}",
    )
    return LeaveOut.model_validate(leave)


# MODIFIÉ : Seul l'admin peut approuver un congé
@router.post("/{leave_id}/approve", response_model=LeaveOut,
             dependencies=[Depends(require_role(Role.admin))])
async def approve_leave(
    leave_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
) -> LeaveOut:
    """Approuver une demande de congé (Admin seulement)."""
    res = await db.execute(select(Leave).where(Leave.id == leave_id))
    leave = res.scalar_one_or_none()
    if not leave:
        raise HTTPException(status_code=404, detail="Demande de congé non trouvée")

    if leave.approved:
         raise HTTPException(status_code=400, detail="Ce congé est déjà approuvé.")

    leave.approved = True
    await db.commit()
    await db.refresh(leave)

    # Récupérer l'employé associé pour l'audit log
    res_emp = await db.execute(select(Employee).where(Employee.id == leave.employee_id))
    employee = res_emp.scalar_one_or_none()

    # Enregistrer l'approbation dans l'audit log
    await log(
        db,
        actor_id=user.id,
        action="approve", # Action: approuver
        entity="leave", # Entité: congé
        entity_id=leave.id,
        branch_id=employee.branch_id if employee else None,
        details=f"Congé approuvé pour Employé ID={leave.employee_id}",
    )
    return LeaveOut.model_validate(leave)


# MODIFIÉ : Seul l'admin peut lister tous les congés
@router.get("/", response_model=list[LeaveOut],
            dependencies=[Depends(require_role(Role.admin))])
async def list_leaves(db: AsyncSession = Depends(get_db)) -> list[LeaveOut]:
    """Lister toutes les demandes de congé (Admin seulement)."""
    res = await db.execute(select(Leave).order_by(Leave.start_date.desc()))
    leaves = res.scalars().all()
    return [LeaveOut.model_validate(x) for x in leaves]
