"""
Dependency wrappers for FastAPI.

This module defines dependencies that provide database sessions and the
currently authenticated user for route handlers.
"""
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import AsyncSessionLocal
from typing import AsyncGenerator, Optional

from .db import get_session
from .auth import get_current_user
from .models import User


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency function that yields an async SQLAlchemy session.
    Ensures the session is closed even if errors occur.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            # You could commit here if needed, but often commits happen in routes
            # await session.commit()
        except Exception:
            await session.rollback() # Rollback on any error during the request
            raise
        # The 'async with' block ensures the session is closed automatically here,
        # whether the request succeeded or failed.


async def api_current_user(user: User = Depends(get_current_user)) -> User:
    """
    (API) FastAPI dependency that yields the currently authenticated user from API Token.
    """
    return user


# --- NOUVELLE DÉPENDANCE : Obtenir les données de l'utilisateur de la session sans redirection (Used by '/') ---
def get_user_data_from_session_safe(request: Request) -> Optional[dict]:
    """
    (WEB) Dependency that yields the user dict from the session, or None if not found.
    NE REDIRIGE PAS et NE LÈVE PAS d'exception.
    """
    return request.session.get("user")
# --- FIN NOUVELLE DÉPENDANCE ---


# --- DÉPENDANCE REDIRIGEANTE (pour les pages PROTÉGÉES) ---
def get_current_session_user(request: Request) -> dict | RedirectResponse:
    """
    (WEB) Dependency that yields the user dict from the session.
    Si l'utilisateur n'est pas trouvé, redirige vers la page de connexion.
    """
    user = request.session.get("user")
    if user is None:
        # Retourner la RedirectResponse directement, Starlette/FastAPI la gère
        return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)
    return user


def web_require_permission(permission: str):
    """
    (WEB) Dependency factory that asserts the user in SESSION has the permission.
    """
    def get_permission_dependency(
        request: Request, # Ajout de request pour la redirection
        user: dict | RedirectResponse = Depends(get_current_session_user)
    ) -> dict | RedirectResponse:
        """Vérifie la permission dans le dictionnaire de la session."""
        
        # Si l'utilisateur n'était pas authentifié, la RedirectResponse est déjà renvoyée
        if isinstance(user, RedirectResponse):
             return user 
        
        permissions = user.get("permissions", {})

        # God Mode
        if permissions.get("is_admin"):
            return user
            
        if permission == "is_admin":
            # Si la permission requise est 'is_admin' et qu'il n'est pas admin, rediriger vers 'home'
            raise HTTPException(
                status_code=status.HTTP_302_FOUND,
                detail="Insufficient permissions (Admin required)",
                headers={"Location": str(request.url_for('home'))}
            )

        if not permissions.get(permission):
            # S'ils sont connectés mais n'ont pas la permission, rediriger vers 'home'
            raise HTTPException(
                status_code=status.HTTP_302_FOUND,
                detail="Insufficient permissions",
                headers={"Location": str(request.url_for('home'))}
            )
        return user
        
    return get_permission_dependency
