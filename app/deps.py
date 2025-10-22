"""
Dependency wrappers for FastAPI.

This module defines dependencies that provide database sessions and the
currently authenticated user for route handlers.
"""
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .auth import get_current_user
from .models import User


async def get_db(session: AsyncSession = Depends(get_session)) -> AsyncSession:
    """FastAPI dependency that yields a database session."""
    return session


async def current_user(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency that yields the currently authenticated user."""
    return user