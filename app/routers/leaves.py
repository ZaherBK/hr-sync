"""
Leave management API endpoints.

Allows managers and administrators to request and approve leaves for employees.
Also provides listing of all leave requests.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import LeaveCreate, LeaveOut
from ..models import Leave, Role
from ..auth import require_role
from ..deps import get_db, current_user
from ..audit import log

router = APIRouter(prefix="/api/leaves", tags=["leaves"])


@router.post("/", response_model=LeaveOut, dependencies=[Depends(require_role(Role.admin, Role.manager))])
async def request_leave(
    payload: LeaveCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
) -> LeaveOut:
    """Request a leave for an employee (admins and managers only)."""
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="Invalid date range")
    leave = Leave(
        employee_id=payload.employee_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        ltype=payload.ltype,
        approved=False,
        created_by=user.id,
    )
    db.add(leave)
    await db.commit()
    await db.refresh(leave)
    await log(
        db,
        actor_id=user.id,
        action="create",
        entity="leave",
        entity_id=leave.id,
        branch_id=user.branch_id,
        details=f"employee={payload.employee_id} {payload.start_date}->{payload.end_date} {payload.ltype.value}",
    )
    return LeaveOut.model_validate(leave)


@router.post("/{leave_id}/approve", response_model=LeaveOut, dependencies=[Depends(require_role(Role.admin, Role.manager))])
async def approve_leave(
    leave_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
) -> LeaveOut:
    """Approve a leave request (admins and managers only)."""
    res = await db.execute(select(Leave).where(Leave.id == leave_id))
    leave = res.scalar_one_or_none()
    if not leave:
        raise HTTPException(status_code=404, detail="Not found")
    leave.approved = True
    await db.commit()
    await db.refresh(leave)
    await log(
        db,
        actor_id=user.id,
        action="approve",
        entity="leave",
        entity_id=leave.id,
        branch_id=user.branch_id,
        details="approved",
    )
    return LeaveOut.model_validate(leave)


@router.get("/", response_model=list[LeaveOut])
async def list_leaves(db: AsyncSession = Depends(get_db)) -> list[LeaveOut]:
    """List all leave requests."""
    res = await db.execute(select(Leave))
    return [LeaveOut.model_validate(x) for x in res.scalars().all()]