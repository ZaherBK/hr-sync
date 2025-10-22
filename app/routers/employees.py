"""
Employee API endpoints.

Enables creation and listing of employees. Creation is allowed for admins and
managers; listing is open to all users.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import EmployeeCreate, EmployeeOut
from ..models import Employee, Role
from ..auth import require_role
from ..deps import get_db

router = APIRouter(prefix="/api/employees", tags=["employees"])


@router.post("/", response_model=EmployeeOut, dependencies=[Depends(require_role(Role.admin, Role.manager))])
async def create_employee(payload: EmployeeCreate, db: AsyncSession = Depends(get_db)):
    """Create a new employee (admins and managers only)."""
    employee = Employee(**payload.model_dump())
    db.add(employee)
    await db.commit()
    await db.refresh(employee)
    return employee


@router.get("/", response_model=list[EmployeeOut])
async def list_employees(db: AsyncSession = Depends(get_db)):
    """List all employees."""
    res = await db.execute(select(Employee))
    return [EmployeeOut.model_validate(x) for x in res.scalars().all()]