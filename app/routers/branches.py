"""
Branch API endpoints.

Provides endpoints to create and list branches. Creation is restricted to
administrators. Listing is open to all authenticated users.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import BranchCreate, BranchOut
from ..models import Branch, Role
from ..auth import require_role
from ..deps import get_db

router = APIRouter(prefix="/api/branches", tags=["branches"])


@router.post("/", response_model=BranchOut, dependencies=[Depends(require_role(Role.admin))])
async def create_branch(payload: BranchCreate, db: AsyncSession = Depends(get_db)):
    """Create a new branch. Only admins may call this endpoint."""
    existing = await db.execute(select(Branch).where(Branch.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Branch exists")
    branch = Branch(name=payload.name, city=payload.city)
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    return branch


@router.get("/", response_model=list[BranchOut])
async def list_branches(db: AsyncSession = Depends(get_db)):
    """List all branches."""
    res = await db.execute(select(Branch))
    return [BranchOut.model_validate(x) for x in res.scalars().all()]