"""
Pydantic models (schemas) for request and response validation.

These schemas define the shapes of input and output data used by the API and
frontend templates. They rely on the ORM models defined in `models.py`.
"""
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field

from .models import Role, AttendanceType, LeaveType


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    role: Role
    branch_id: Optional[int] = None
    is_active: bool = True


class UserCreate(UserBase):
    password: str = Field(min_length=6)


class UserOut(UserBase):
    id: int

    class Config:
        from_attributes = True


class BranchBase(BaseModel):
    name: str
    city: str


class BranchCreate(BranchBase):
    pass


class BranchOut(BranchBase):
    id: int

    class Config:
        from_attributes = True


class EmployeeBase(BaseModel):
    first_name: str
    last_name: str
    position: str
    branch_id: int
    active: bool = True


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeOut(EmployeeBase):
    id: int

    class Config:
        from_attributes = True


class AttendanceCreate(BaseModel):
    employee_id: int
    date: date
    atype: AttendanceType
    note: Optional[str] = None


class AttendanceOut(BaseModel):
    id: int
    employee_id: int
    date: date
    atype: AttendanceType
    note: Optional[str]
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


class LeaveCreate(BaseModel):
    employee_id: int
    start_date: date
    end_date: date
    ltype: LeaveType


class LeaveOut(BaseModel):
    id: int
    employee_id: int
    start_date: date
    end_date: date
    ltype: LeaveType
    approved: bool
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


class AuditOut(BaseModel):
    id: int
    actor_id: int
    action: str
    entity: str
    entity_id: Optional[int]
    branch_id: Optional[int]
    details: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True