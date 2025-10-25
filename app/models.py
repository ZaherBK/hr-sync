"""
Modèles ORM de la base de données pour Bijouterie Zaher App.
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
    Numeric, # Pour les montants
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# --- SUPPRIMÉ ---
# class Role(str, enum.Enum):
#     admin = "admin"
#     manager = "manager"
# --- FIN SUPPRIMÉ ---

# --- NOUVEAU MODÈLE : Role ---
class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    
    # --- Permissions ---
    # "God Mode" - contourne toutes les autres vérifications
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False) 
    
    # Gestion du système
    can_manage_users: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_roles: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_branches: Mapped[bool] = mapped_column(Boolean, default=False)
    can_view_settings: Mapped[bool] = mapped_column(Boolean, default=False)
    can_clear_logs: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Gestion RH
    can_manage_employees: Mapped[bool] = mapped_column(Boolean, default=False)
    can_view_reports: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Gestion quotidienne
    can_manage_pay: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_absences: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_leaves: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_deposits: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relations
    users = relationship("User", back_populates="role")

    def to_dict(self):
        """Renvoie les permissions sous forme de dictionnaire."""
        return {
            "id": self.id,
            "name": self.name,
            "is_admin": self.is_admin,
            "can_manage_users": self.can_manage_users,
            "can_manage_roles": self.can_manage_roles,
            "can_manage_branches": self.can_manage_branches,
            "can_view_settings": self.can_view_settings,
            "can_clear_logs": self.can_clear_logs,
            "can_manage_employees": self.can_manage_employees,
            "can_view_reports": self.can_view_reports,
            "can_manage_pay": self.can_manage_pay,
            "can_manage_absences": self.can_manage_absences,
            "can_manage_leaves": self.can_manage_leaves,
            "can_manage_deposits": self.can_manage_deposits,
        }
# --- FIN NOUVEAU MODÈLE ---


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255))
    
    # --- MODIFIÉ : Utilise la clé étrangère vers la table roles ---
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"))
    # --- FIN MODIFIÉ ---
    
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Relations
    # --- MODIFIÉ : Relation vers le modèle Role ---
    role = relationship("Role", back_populates="users", lazy="joined")
    # --- FIN MODIFIÉ ---
    branch = relationship("Branch", back_populates="users")


class Branch(Base): # Magasin
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    city: Mapped[str] = mapped_column(String(120))
    # Relations
    users = relationship("User", back_populates="branch")
    employees = relationship("Employee", back_populates="branch")


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(120))
    last_name: Mapped[str] = mapped_column(String(120))
    cin: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True, index=True)
    position: Mapped[str] = mapped_column(String(120))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # --- NOUVEAU CHAMP : Salaire ---
    salary: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True) # Salaire de base
    
    # Relations
    branch = relationship("Branch", back_populates="employees")
    attendances = relationship("Attendance", back_populates="employee")
    leaves = relationship("Leave", back_populates="employee")
    deposits = relationship("Deposit", back_populates="employee")
    pay_history = relationship("Pay", back_populates="employee") # --- NOUVELLE RELATION ---


class AttendanceType(str, enum.Enum):
    present = "present"
    absent = "absent"   # On n'utilisera que 'absent'


class Attendance(Base):
    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    atype: Mapped[AttendanceType] = mapped_column(Enum(AttendanceType))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Relations
    employee = relationship("Employee", back_populates="attendances")


class LeaveType(str, enum.Enum):
    paid = "paid"
    unpaid = "unpaid"
    sick = "sick"


class Leave(Base): # Congé
    __tablename__ = "leaves"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    ltype: Mapped[LeaveType] = mapped_column(Enum(LeaveType))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Relations
    employee = relationship("Employee", back_populates="leaves")


class Deposit(Base): # Avance
    __tablename__ = "deposits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    date: Mapped[date] = mapped_column(Date, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Relations
    employee = relationship("Employee", back_populates="deposits")

# --- NOUVEAU MODÈLE : Paie (Pay) ---
class PayType(str, enum.Enum):
    hebdomadaire = "hebdomadaire" # Hebdomadaire (par semaine)
    mensuel = "mensuel"       # Mensuel (par mois)

class Pay(Base):
    __tablename__ = "pay_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    amount: Mapped[float] = mapped_column(Numeric(10, 2)) # Montant payé
    date: Mapped[date] = mapped_column(Date, index=True) # Date du paiement
    pay_type: Mapped[PayType] = mapped_column(Enum(PayType)) # Type de paie
    note: Mapped[str | None] = mapped_column(Text, nullable=True) # Note/Description
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id")) # Payé par
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Relations
    employee = relationship("Employee", back_populates="pay_history")
# --- FIN DU NOUVEAU MODÈLE ---


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
