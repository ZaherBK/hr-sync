"""
Database ORM models for HR Sync.

Defines the SQLAlchemy models for users, branches, employees, attendance
records, leaves, and audit logs. Enum classes capture the possible roles and
types for attendance and leaves.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Role(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    staff = "staff"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.manager)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Relationships
    branch = relationship("Branch", back_populates="users")


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    city: Mapped[str] = mapped_column(String(120))
    # Relationships
    users = relationship("User", back_populates="branch")
    employees = relationship("Employee", back_populates="branch")


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(120))
    last_name: Mapped[str] = mapped_column(String(120))
    position: Mapped[str] = mapped_column(String(120))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Relationships
    branch = relationship("Branch", back_populates="employees")
    attendances = relationship("Attendance", back_populates="employee")
    leaves = relationship("Leave", back_populates="employee")


class AttendanceType(str, enum.Enum):
    present = "present"
    absent = "absent"


class Attendance(Base):
    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    atype: Mapped[AttendanceType] = mapped_column(Enum(AttendanceType))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Relationships
    employee = relationship("Employee", back_populates="attendances")


class LeaveType(str, enum.Enum):
    paid = "paid"
    unpaid = "unpaid"
    sick = "sick"


class Leave(Base):
    __tablename__ = "leaves"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    ltype: Mapped[LeaveType] = mapped_column(Enum(LeaveType))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Relationships
    employee = relationship("Employee", back_populates="leaves")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(120))
    entity: Mapped[str] = mapped_column(String(120))
    entity_id: Mapped[int | None]
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())