"""
Database configuration and session factory.

This module reads the `DATABASE_URL` environment variable and sets up an
asynchronous SQLAlchemy engine. For asyncpg connections, it normalizes the URL
to ensure SSL is enabled and unsupported query parameters are removed. A
session generator is provided for FastAPI dependency injection.
"""
import os
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from fastapi import Depends  # <--- FIX: AJOUTÉ L'IMPORTATION MANQUANTE

def _normalize_asyncpg_url(url: str) -> tuple[str, dict]:
    """Normalize an asyncpg URL, cleaning up query params and adding SSL.

    asyncpg does not support `sslmode` or `channel_binding` query parameters,
    but it accepts `ssl=true` or an SSL context passed via connect_args. This
    function removes unsupported parameters and ensures SSL is requested. It
    returns a tuple of the updated URL and a dictionary of connect_args to
    pass to SQLAlchemy's engine.
    """
    if not url:
        return url, {}

    # Only modify asyncpg URLs
    if url.startswith("postgresql+asyncpg://"):
        parts = urlparse(url)
        query_dict = dict(parse_qsl(parts.query, keep_blank_values=True))
        # Remove unsupported parameters
        query_dict.pop("sslmode", None)
        query_dict.pop("channel_binding", None)
        # Ensure SSL is set
        query_dict.setdefault("ssl", "true")
        new_query = urlencode(query_dict)
        new_url = urlunparse((parts.scheme, parts.netloc, parts.path, "", new_query, ""))
        return new_url, {"ssl": True}
    return url, {}


# Read the database URL from the environment or default to a local SQLite DB
DATABASE_URL_RAW = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./hr.db")
DATABASE_URL, CONNECT_ARGS = _normalize_asyncpg_url(DATABASE_URL_RAW)

# Create the asynchronous engine and session factory
engine = create_async_engine(DATABASE_URL, echo=False, future=True, connect_args=CONNECT_ARGS)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Base class for ORM models
Base = declarative_base()


#
# --- DEBUT DE LA CORRECTION ---
#
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    (Correct) Yield an asynchronous session for use with FastAPI dependencies.
    This handles session creation, commit-on-success, rollback-on-error,
    and closing.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db(session: AsyncSession = Depends(get_session)) -> AsyncSession:
    """
    FastAPI dependency that yields the database session from get_session.
    This is now just a simple wrapper.
    """
    yield session
#
# --- FIN DE LA CORRECTION ---
#
