"""
Modèles ORM de la base de données pour Bijouterie Zaher App.

Définit les modèles SQLAlchemy pour les utilisateurs, magasins, employés, présences,
congés, avances (dépôts) et journaux d'audit. Les classes Enum capturent les rôles possibles
et les types pour la présence et les congés.
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
    Numeric, # Ajouté pour le montant des avances
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Role(str, enum.Enum):
    admin = "admin"      # Propriétaire / Admin
    manager = "manager"  # Manager de magasin
    # staff = "staff"    # (Commenté - Pas utilisé actuellement mais peut être ajouté plus tard)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.manager)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"), nullable=True) # ID du magasin associé
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Relations
    branch = relationship("Branch", back_populates="users") # Relation vers le magasin


class Branch(Base): # Représente un magasin (Shop)
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True) # Nom du magasin (ex: "Magasin Ariana")
    city: Mapped[str] = mapped_column(String(120)) # Ville
    # Relations
    users = relationship("User", back_populates="branch") # Utilisateurs (managers) dans ce magasin
    employees = relationship("Employee", back_populates="branch") # Employés dans ce magasin


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(120)) # Prénom
    last_name: Mapped[str] = mapped_column(String(120)) # Nom
    cin: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True, index=True) # Numéro CIN (Carte d'Identité Nationale) - Ajouté
    position: Mapped[str] = mapped_column(String(120)) # Poste occupé
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id")) # Magasin associé
    active: Mapped[bool] = mapped_column(Boolean, default=True) # Statut (Actif/Inactif)
    # Relations
    branch = relationship("Branch", back_populates="employees")
    attendances = relationship("Attendance", back_populates="employee")
    leaves = relationship("Leave", back_populates="employee")
    deposits = relationship("Deposit", back_populates="employee") # Relation vers les avances - Ajouté


class AttendanceType(str, enum.Enum):
    present = "present" # Présent
    absent = "absent"   # Absent


class Attendance(Base):
    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    atype: Mapped[AttendanceType] = mapped_column(Enum(AttendanceType)) # Type (Présent/Absent)
    note: Mapped[str | None] = mapped_column(Text, nullable=True) # Note
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id")) # ID de l'utilisateur qui a créé l'enregistrement
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now()) # Date de création
    # Relations
    employee = relationship("Employee", back_populates="attendances")


class LeaveType(str, enum.Enum):
    paid = "paid"     # Payé
    unpaid = "unpaid" # Non payé
    sick = "sick"     # Maladie


class Leave(Base): # Représente un congé (Vacation)
    __tablename__ = "leaves"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    start_date: Mapped[date] = mapped_column(Date) # Date de début
    end_date: Mapped[date] = mapped_column(Date)   # Date de fin
    ltype: Mapped[LeaveType] = mapped_column(Enum(LeaveType)) # Type de congé
    approved: Mapped[bool] = mapped_column(Boolean, default=False) # Approuvé ou non
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id")) # Créé par
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now()) # Date de création
    # Relations
    employee = relationship("Employee", back_populates="leaves")

# --- NOUVEAU MODÈLE : Deposit (Avance) ---
class Deposit(Base):
    __tablename__ = "deposits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    amount: Mapped[float] = mapped_column(Numeric(10, 2)) # Montant de l'avance (ex: 1250.75)
    date: Mapped[date] = mapped_column(Date, index=True) # Date de l'avance
    note: Mapped[str | None] = mapped_column(Text, nullable=True) # Note/Description
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id")) # Créé par
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now()) # Date de création
    # Relations
    employee = relationship("Employee", back_populates="deposits")
# --- FIN DU NOUVEAU MODÈLE ---

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id")) # Qui a fait l'action
    action: Mapped[str] = mapped_column(String(120)) # Type d'action (create, update, delete, approve)
    entity: Mapped[str] = mapped_column(String(120)) # Entité concernée (employee, leave, deposit...)
    entity_id: Mapped[int | None] # ID de l'entité
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"), nullable=True) # Magasin concerné (si applicable)
    details: Mapped[str | None] = mapped_column(Text, nullable=True) # Détails supplémentaires
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now()) # Quand l'action a eu lieu
