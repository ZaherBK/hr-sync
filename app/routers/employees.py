from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import EmployeeCreate, EmployeeOut
from ..models import Employee
# --- MODIFIÉ ---
from ..auth import api_require_permission
# --- FIN MODIFIÉ ---
from ..deps import get_db

router = APIRouter(prefix="/api/employees", tags=["employees"])

# --- MODIFIÉ : Utilise la nouvelle dépendance de permission ---
@router.post("/", response_model=EmployeeOut, dependencies=[Depends(api_require_permission("can_manage_employees"))])
# --- FIN MODIFIÉ ---
async def create_employee(payload: EmployeeCreate, db: AsyncSession = Depends(get_db)):
    """Create a new employee."""
    # ... (le reste de la logique reste identique) ...
    if payload.cin:
        exists = await db.execute(select(Employee).where(Employee.cin == payload.cin))
        if exists.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="CIN already exists")

    employee = Employee(**payload.model_dump())
    db.add(employee)
    await db.commit()
    await db.refresh(employee)
    return employee


@router.get("/", response_model=list[EmployeeOut])
async def list_employees(db: AsyncSession = Depends(get_db)):
    """List all active employees."""
    res = await db.execute(select(Employee).where(Employee.active == True))
    return res.scalars().all()
