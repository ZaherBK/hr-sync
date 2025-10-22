"""
Attendance API endpoints.

Allows managers and administrators to record attendance for employees and list
all attendance records.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import AttendanceCreate, AttendanceOut
from ..models import Attendance, Role
from ..auth import require_role
from ..deps import get_db, current_user
from ..audit import log

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


@router.post("/", response_model=AttendanceOut, dependencies=[Depends(require_role(Role.admin, Role.manager))])
async def mark_attendance(
    payload: AttendanceCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
) -> AttendanceOut:
    """Record attendance for an employee (admins and managers only)."""
    attendance = Attendance(
        employee_id=payload.employee_id,
        date=payload.date,
        atype=payload.atype,
        note=payload.note,
        created_by=user.id,
    )
    db.add(attendance)
    await db.commit()
    await db.refresh(attendance)
    # Log the action
    await log(
        db,
        actor_id=user.id,
        action="create",
        entity="attendance",
        entity_id=attendance.id,
        branch_id=user.branch_id,
        details=f"employee={payload.employee_id} {payload.atype.value} {payload.date}",
    )
    return AttendanceOut.model_validate(attendance)


@router.get("/", response_model=list[AttendanceOut])
async def list_attendance(db: AsyncSession = Depends(get_db)) -> list[AttendanceOut]:
    """List all attendance records."""
    res = await db.execute(select(Attendance))
    return [AttendanceOut.model_validate(x) for x in res.scalars().all()]