from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from ..schemas import DepositCreate, DepositOut
from ..models import Deposit, Employee, User
# --- MODIFIÉ ---
from ..auth import api_require_permission
from ..deps import get_db, api_current_user # Renommé
# --- FIN MODIFIÉ ---

router = APIRouter(prefix="/api/deposits", tags=["deposits"])

# --- MODIFIÉ : Utilise la nouvelle dépendance de permission ---
@router.post("/", response_model=DepositOut, dependencies=[Depends(api_require_permission("can_manage_deposits"))])
# --- FIN MODIFIÉ ---
async def create_deposit(
    payload: DepositCreate, 
    db: AsyncSession = Depends(get_db), 
    user: User = Depends(api_current_user) # Renommé
):
    """Create a new deposit (advance)."""
    # Validation
    res = await db.execute(select(Employee).where(Employee.id == payload.employee_id))
    employee = res.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    # --- MODIFIÉ : Vérification de permission par branche ---
    if not user.role.is_admin and user.branch_id != employee.branch_id:
        raise HTTPException(status_code=403, detail="Not authorized for this branch")
    # --- FIN MODIFIÉ ---

    deposit = Deposit(
        **payload.model_dump(),
        created_by=user.id
    )
    db.add(deposit)
    await db.commit()
    await db.refresh(deposit)
    return deposit


@router.get("/", response_model=List[DepositOut])
async def list_deposits(db: AsyncSession = Depends(get_db)):
    """List all deposits."""
    res = await db.execute(select(Deposit))
    return res.scalars().all()
