"""
Authentication and authorization utilities for HR Sync.

Provides password hashing and verification, JWT token creation and validation,
and dependency functions for FastAPI to authenticate users and enforce
role-based access control.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
# --- AJOUTÉ ---
from sqlalchemy.orm import selectinload
# --- FIN AJOUTÉ ---

from .db import get_session
# --- MODIFIÉ : Role n'est plus un Enum ---
from .models import User
# --- FIN MODIFIÉ ---
from .schemas import Token

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme for FastAPI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login") # Note: /api/users/login est dans users.router

# JWT configuration
SECRET_KEY = os.getenv("SECRET_KEY", "change_me")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))


def hash_password(password: str) -> str:
    """Hash a plain password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    return pwd_context.verify(password, hashed_password)


async def authenticate_user(session: AsyncSession, email: str, password: str) -> Optional[User]:
    """Return a user if authentication succeeds; otherwise, None."""
    # --- MODIFIÉ : Eager load le rôle ---
    res = await session.execute(
        select(User).options(selectinload(User.role)).where(User.email == email)
    )
    # --- FIN MODIFIÉ ---
    user = res.scalar_one_or_none()
    if not user or not verify_password(password, user.hashed_password) or not user.is_active:
        return None
    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT with an expiration time."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def login(form_data: OAuth2PasswordRequestForm = Depends(), session: AsyncSession = Depends(get_session)) -> Token:
    """FastAPI dependency for the login endpoint; returns a JWT token."""
    user = await authenticate_user(session, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # --- MODIFIÉ : Stocke les permissions dans le token ---
    # Le token contient maintenant les permissions pour que get_current_user les ait.
    permissions = user.role.to_dict() if user.role else {}
    token_data = {
        "sub": str(user.id),
        "branch_id": user.branch_id,
        "permissions": permissions # Inclure tout le dict de permissions
    }
    # --- FIN MODIFIÉ ---
    
    token = create_access_token(token_data)
    return Token(access_token=token)


async def get_current_user(token: str = Depends(oauth2_scheme), session: AsyncSession = Depends(get_session)) -> User:
    """Retrieve the current user from a JWT token."""
    credentials_exception = HTTPException(status_code=401, detail="Invalid credentials")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid = payload.get("sub")
        if uid is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    # --- MODIFIÉ : Eager load le rôle pour vérifier les permissions ---
    res = await session.execute(
        select(User).options(selectinload(User.role)).where(User.id == int(uid))
    )
    # --- FIN MODIFIÉ ---
    
    user = res.scalar_one_or_none()
    if not user or not user.is_active:
        raise credentials_exception
    return user


# --- NOUVELLE FONCTION : Remplacement de require_role pour l'API ---
def api_require_permission(permission: str):
    """
    Dependency factory that asserts the current API user has the specified permission.
    Utilise 'is_admin' comme "God Mode".
    """
    async def dep(user: User = Depends(get_current_user)) -> User:
        if not user.role:
            raise HTTPException(status_code=403, detail="Insufficient permissions (no role assigned)")

        # God Mode
        if user.role.is_admin:
            return user
        
        # Vérification spécifique
        if not getattr(user.role, permission, False):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
            
        return user
    return dep
# --- FIN NOUVELLE FONCTION ---
