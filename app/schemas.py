"""
Modèles Pydantic (schémas) pour la validation des requêtes et réponses.

Ces schémas définissent la structure des données d'entrée et de sortie utilisées par l'API
et les templates frontend. Ils s'appuient sur les modèles ORM définis dans `models.py`.
"""
from datetime import date, datetime
from typing import List, Optional

# Utiliser `Decimal` pour les montants monétaires pour éviter les erreurs de virgule flottante
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field, field_validator

# Importer les Enums depuis models.py
from .models import Role, AttendanceType, LeaveType


# --- Schémas Utilisateur ---
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserBase(BaseModel):
    email: EmailStr
    full_name: str # Nom complet
    role: Role # Rôle (admin, manager)
    branch_id: Optional[int] = None # ID du magasin associé
    is_active: bool = True # Compte actif ?


class UserCreate(UserBase):
    password: str = Field(min_length=6)


class UserOut(UserBase):
    id: int

    class Config:
        from_attributes = True # Permet de créer le schéma depuis un objet ORM


# --- Schémas Magasin (Branch) ---
class BranchBase(BaseModel):
    name: str # Nom du magasin
    city: str # Ville


class BranchCreate(BranchBase):
    pass # Identique à BranchBase pour la création


class BranchOut(BranchBase):
    id: int

    class Config:
        from_attributes = True


# --- Schémas Employé ---
class EmployeeBase(BaseModel):
    first_name: str # Prénom
    last_name: str # Nom
    cin: Optional[str] = Field(None, max_length=20) # CIN (Carte d'Identité Nationale) - Ajouté
    position: str # Poste
    branch_id: int # ID du magasin associé
    active: bool = True # Statut actif ?

    # Validateur pour s'assurer que le CIN est numérique si fourni
    @field_validator('cin')
    def validate_cin(cls, v):
        if v is not None and not v.isdigit():
            raise ValueError('Le numéro CIN doit être composé uniquement de chiffres.')
        return v


class EmployeeCreate(EmployeeBase):
    pass # Identique à EmployeeBase pour la création


class EmployeeOut(EmployeeBase):
    id: int

    class Config:
        from_attributes = True


# --- Schémas Présence (Attendance) ---
class AttendanceCreate(BaseModel):
    employee_id: int
    date: date
    atype: AttendanceType # Type (present, absent)
    note: Optional[str] = None # Note


class AttendanceOut(BaseModel):
    id: int
    employee_id: int
    date: date
    atype: AttendanceType
    note: Optional[str]
    created_by: int # Créé par (ID utilisateur)
    created_at: datetime # Date de création

    class Config:
        from_attributes = True


# --- Schémas Congé (Leave) ---
class LeaveCreate(BaseModel):
    employee_id: int
    start_date: date # Date début
    end_date: date # Date fin
    ltype: LeaveType # Type (paid, unpaid, sick)

    # Validateur pour s'assurer que la date de fin n'est pas avant la date de début
    @field_validator('end_date')
    def validate_end_date(cls, v, values):
        # Utiliser context=values.data dans Pydantic v2 ou values dans v1
        if 'start_date' in values.data and v < values.data['start_date']:
             raise ValueError('La date de fin ne peut pas être antérieure à la date de début.')
        return v


class LeaveOut(BaseModel):
    id: int
    employee_id: int
    start_date: date
    end_date: date
    ltype: LeaveType
    approved: bool # Approuvé ?
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True

# --- NOUVEAUX SCHÉMAS : Avance (Deposit) ---
class DepositBase(BaseModel):
    employee_id: int
    # Utiliser Decimal pour la précision monétaire
    amount: Decimal = Field(..., gt=0, max_digits=10, decimal_places=2) # Montant (> 0)
    date: date
    note: Optional[str] = None # Note/Description


class DepositCreate(DepositBase):
    pass # Identique à DepositBase pour la création


class DepositOut(DepositBase):
    id: int
    created_by: int # Créé par (ID utilisateur)
    created_at: datetime # Date de création

    class Config:
        from_attributes = True
# --- FIN DES NOUVEAUX SCHÉMAS ---


# --- Schéma Journal d'Audit ---
class AuditOut(BaseModel):
    id: int
    actor_id: int # Qui a fait l'action
    action: str # Type d'action
    entity: str # Entité concernée
    entity_id: Optional[int] # ID de l'entité
    branch_id: Optional[int] # Magasin concerné
    details: Optional[str] # Détails
    created_at: datetime # Quand

    class Config:
        from_attributes = True
