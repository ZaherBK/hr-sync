"""
Modèles Pydantic (schémas) pour la validation des requêtes et réponses.
"""
from datetime import date, datetime
from typing import List, Optional
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict

# Importer les Enums depuis models.py, y compris PayType
from .models import Role, AttendanceType, LeaveType, PayType


# --- Schémas Utilisateur ---
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
    model_config = ConfigDict(from_attributes=True)


# --- Schémas Magasin (Branch) ---
class BranchBase(BaseModel):
    name: str
    city: str

class BranchCreate(BranchBase):
    pass

class BranchOut(BranchBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


# --- Schémas Employé ---
class EmployeeBase(BaseModel):
    first_name: str
    last_name: str
    cin: Optional[str] = Field(None, max_length=20)
    position: str
    branch_id: int
    active: bool = True
    
    # --- NOUVEAU CHAMP : Salaire ---
    salary: Optional[Decimal] = Field(None, gt=0, max_digits=10, decimal_places=2)

    @field_validator('cin')
    def validate_cin(cls, v):
        if v is not None and v != "" and not v.isdigit():
            raise ValueError('Le numéro CIN doit être composé uniquement de chiffres.')
        return v
    
    @field_validator('salary')
    def validate_salary(cls, v):
        if v is not None and v < 0:
            raise ValueError('Le salaire doit être un montant positif.')
        return v
    
    model_config = ConfigDict(from_attributes=True)


class EmployeeCreate(EmployeeBase):
    pass

class EmployeeOut(EmployeeBase):
    id: int


# --- Schémas Présence (Attendance) ---
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
    model_config = ConfigDict(from_attributes=True)


# --- Schémas Congé (Leave) ---
class LeaveCreate(BaseModel):
    employee_id: int
    start_date: date
    end_date: date
    ltype: LeaveType
    
    model_config = ConfigDict(from_attributes=True)

    @field_validator('end_date')
    def validate_end_date(cls, v, info):
        if 'start_date' in info.data and v < info.data['start_date']:
             raise ValueError('La date de fin ne peut pas être antérieure à la date de début.')
        return v


class LeaveOut(BaseModel):
    id: int
    employee_id: int
    start_date: date
    end_date: date
    ltype: LeaveType
    approved: bool
    created_by: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- Schémas Avance (Deposit) ---
class DepositBase(BaseModel):
    employee_id: int
    amount: Decimal = Field(..., gt=0, max_digits=10, decimal_places=2)
    date: date
    note: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class DepositCreate(DepositBase):
    pass

class DepositOut(DepositBase):
    id: int
    created_by: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- NOUVEAUX SCHÉMAS : Paie (Pay) ---
class PayBase(BaseModel):
    employee_id: int
    amount: Decimal = Field(..., gt=0, max_digits=10, decimal_places=2)
    date: date
    pay_type: PayType # Utiliser l'Enum
    note: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class PayCreate(PayBase):
    pass

class PayOut(PayBase):
    id: int
    created_by: int
    created_at: datetime
# --- FIN DES NOUVEAUX SCHÉMAS ---


# --- Schéma Journal d'Audit ---
class AuditOut(BaseModel):
    id: int
    actor_id: int
    action: str
    entity: str
    entity_id: Optional[int]
    branch_id: Optional[int]
    details: Optional[str]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
